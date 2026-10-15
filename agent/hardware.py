"""
Hardware detection and the node registration file (issue #95).

Before this module nothing wrote ``nodes/<node_id>.yaml``, so no node could ever
enter the cluster. ``backend/services/syncthing_service.py`` reads that file
into a ``NodeSpec`` and writes it to the store; until an agent produces one the
cluster has zero nodes, the heartbeat monitor has nothing to act on, and the
dashboard's node pages stay empty. Worse, the handler wraps everything in a
bare ``except`` that logs and drops, so a *malformed* file is indistinguishable
from an agent that is not running.

Field-by-field detection strategy (spec §5). ``Notes`` is where the value is
*changed* on the way into the file, which is what separates a reading from a
rule:

Field           Source                          Notes
--------------- ------------------------------- --------------------------
node_id         settings.NODE_ID                never probed
hostname        platform.node()                 the machine's own name
cpus            psutil.cpu_count()              logical, floored at 1
memory_gb       psutil.virtual_memory().total   // 1024**3, floored at 1
gpus            gputil.getGPUs(), guarded       [] when unavailable
os              platform.system().lower()       linux/darwin/windows
status          literal "ONLINE"                monitor owns OFFLINE
last_heartbeat  datetime.now(timezone.utc)      aware UTC, store is tz-aware
current_job_id  None                            not set here (#97)

**The clock is read as aware UTC.** ``last_heartbeat`` lands in a SQLite
``DateTime(timezone=True)`` column (via #121), which preserves the offset on
write and expects it on read. ``SyncthingService._heartbeat_monitor`` compares
it against ``datetime.now(timezone.utc)`` — both sides are aware UTC, so the
delta is correct on any host timezone. The previous naive-local convention
caused a healthy node to be marked ``OFFLINE`` on non-UTC hosts (#121).

``platform.node()`` rather than ``socket.gethostname()``: they answer the same
question -- on POSIX the first is ``uname().nodename``, which is what
``gethostname()`` returns -- and using the ``platform`` module keeps ``socket``
out of the agent's import surface. That surface is pinned by
``test_agent_imports_no_network_module`` (issue #93 AC-15): the agent is
file-only by decision, and ``socket`` is precisely the module that decision is
about.

Degradation contract -- a node with no GPU is normal, not an error:

Situation                               Result
-------------------------------------- ------------------
gputil importable, getGPUs() returns    one GPUInfo per device
gputil importable, raises or returns []  gpus: []
gputil not installed (today's venv)      gpus: []
one device's attributes raise           that one is skipped

``gpus: []`` is written as an empty list, never omitted: ``gpus`` is a required
field of ``NodeSpec`` and an omitted key fails validation, which the backend
then drops without a visible failure.

The probe is a **pure** function of its arguments so it survives a transport
change -- if registration ever moves from a replicated file to HTTP, only
:func:`write_node_spec` is transport-specific and this detection logic carries
over unchanged (spec Q4).
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Sequence

import psutil

from agent.paths import AgentPaths
from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.node_spec import GPUInfo, NodeSpec

logger = logging.getLogger(__name__)

#: 1 GiB, the unit ``memory_gb`` is counted in.
BYTES_PER_GIB: Final[int] = 1024**3

#: ``gputil`` reports device memory in MiB, and ``GPUInfo.memory_gb`` is GiB.
BYTES_PER_MIB: Final[int] = 1024

#: The floors are load-bearing, not defensive noise. ``cpus`` and ``memory_gb``
#: carry ``ge=1`` in ``NodeSpec``, and a value below that is rejected by the
#: backend's bare ``except``, i.e. the node vanishes with nothing logged.
#: ``memory_gb`` truncates rather than rounds: this development box reports
#: 3.70 GiB and yields 3, and a machine under 1 GiB would yield 0 -- which
#: ``ge=1`` rejects outright. A real, small machine must register.
MINIMUM_CPUS: Final[int] = 1
MINIMUM_MEMORY_GB: Final[int] = 1

#: The only ``status`` the agent ever writes. The heartbeat monitor flips
#: ``ONLINE`` -> ``OFFLINE`` in the store from heartbeat staleness, so anything
#: else here is fought within one 30 s tick (spec §6).
REGISTERED_STATUS: Final[str] = "ONLINE"


def probe_hardware(
    *,
    node_id: str,
    hostname: str,
    cpus: int,
    memory_bytes: int,
    gpus: Sequence[GPUInfo],
    os_name: str,
    now: datetime,
) -> NodeSpec:
    """Assemble a ``NodeSpec`` from already-collected facts. Pure: no syscalls.

    ``now`` is a required argument rather than an optional one defaulting to the
    clock for the same reason: a function that reads the time is not pure, and a
    test asserting the resulting ``NodeSpec`` exactly could not pin
    ``last_heartbeat``.

    This is where the floors are applied, not in the detectors: the floors are a
    property of the *schema*, so they belong to the one place that knows the
    schema, and a caller that has already clamped a value is not double-charged
    for it.

    ``now`` is stored verbatim -- no conversion, no normalisation -- so the
    caller owns the clock convention. That is deliberate: a pure function that
    silently reinterpreted its input's timezone would be hiding exactly the
    decision this issue had to make explicit.
    """
    return NodeSpec(
        node_id=node_id,
        hostname=hostname,
        gpus=list(gpus),
        cpus=max(int(cpus), MINIMUM_CPUS),
        memory_gb=max(int(memory_bytes) // BYTES_PER_GIB, MINIMUM_MEMORY_GB),
        os=os_name,
        status=REGISTERED_STATUS,
        last_heartbeat=now,
        current_job_id=None,
    )


def collect_node_spec(node_id: str, *, now: datetime | None = None) -> NodeSpec:
    """Probe this machine and return a ``NodeSpec``. The only impure entry point.

    ``now`` exists so a caller can pass a fixed instant; the loop does not,
    because the whole point of the file is that it says *now*.

    Aware UTC — the store's column is ``DateTime(timezone=True)`` (via #121)
    and the monitor compares against ``datetime.now(timezone.utc)``. Writing
    naive local would be misread as UTC and inflate the delta by the host's
    offset on non-UTC machines.
    """
    return probe_hardware(
        node_id=node_id,
        hostname=_hostname(),
        cpus=_logical_cpu_count(),
        memory_bytes=_total_memory_bytes(),
        gpus=_gpu_infos(),
        os_name=_os_name(),
        now=datetime.now(timezone.utc) if now is None else now,
    )


def write_node_spec(paths: AgentPaths, spec: NodeSpec) -> Path:
    """Write ``spec`` to the node file ``paths`` already resolved, and log it.

    The path comes from :class:`AgentPaths` and is never rebuilt here. The agent
    and the backend watcher have to agree on one convention, and ``resolve_paths``
    already mirrors ``get_node_state_file``; a hand-built string in a second
    place is how the two drift (#95 AC-6).

    ``write_yaml`` is atomic -- temp file, then ``os.replace`` -- which is what
    makes the rewrite safe to do every 30 s against a watcher that may read the
    file at any moment: a reader sees either the previous file or the complete
    new one, never a truncated document, and a truncated document is one the
    backend would drop without a visible failure.

    The resolved path is logged at INFO on every success, deliberately: when a
    node is missing from the dashboard the first question is whether the agent
    wrote the file the watcher is looking at, and a log line is the only place
    that answer can come from (spec §8).
    """
    write_yaml(str(paths.node_file), spec)
    logger.info("registered %s at %s", spec.node_id, paths.node_file)
    return paths.node_file


def _hostname() -> str:
    return platform.node()


def _os_name() -> str:
    # Deliberately the normalised platform name and not a kernel string: ``os``
    # is free-form, so pinning it to a comparable value is what makes it
    # sortable across a fleet, and a kernel version would put a diff in every
    # replicated file on every patch bump for no operational gain (spec Q2).
    return platform.system().lower()


def _logical_cpu_count() -> int:
    # ``cpu_count()`` returns None rather than 0 on a few platforms; both reach
    # ``probe_hardware``, which floors them.
    return int(psutil.cpu_count() or 0)


def _total_memory_bytes() -> int:
    return int(psutil.virtual_memory().total)


def _gputil() -> Any:
    """Import ``gputil`` at call time, or report its absence as ``None``.

    Imported lazily rather than at module scope for two reasons: the agent has to
    import cleanly on a machine where the declared-but-not-installed dependency
    is absent (issue #93 AC-16 imports every shipped module in a bare
    subprocess), and an import at module scope would make "is ``gputil``
    available?" a decision made once at import rather than once per probe.
    """
    try:
        import gputil
    except ImportError:
        logger.debug("gputil is not installed; registering with no GPUs")
        return None
    return gputil


def _gpu_infos() -> list[GPUInfo]:
    """Enumerate GPUs, degrading to ``[]`` rather than failing.

    Every arm of the contract in the module docstring is here. ``getGPUs``
    raises ``ValueError`` on a machine with no NVIDIA driver, which is the normal
    case for a laptop or a container, and it raises other things on machines
    where ``nvidia-smi`` hangs; neither is a reason to refuse to register.
    """
    gputil = _gputil()
    if gputil is None:
        return []

    try:
        devices = gputil.getGPUs()
    except Exception:
        logger.warning(
            "gputil.getGPUs() failed; registering with no GPUs", exc_info=True
        )
        return []

    gpus: list[GPUInfo] = []
    for device in devices:
        try:
            name = str(device.name).strip()
            memory_gb = int(device.memoryTotal) // BYTES_PER_MIB
        except Exception:
            # One unreadable device must not cost the others: a machine with four
            # GPUs and a broken one is still a three-GPU node, and reporting one
            # fewer is strictly better than not registering at all.
            logger.warning("skipping a GPU that could not be read", exc_info=True)
            continue
        if not name:
            # ``GPUInfo.name`` has no constraint, so an empty string would
            # validate and reach the dashboard as a nameless row.
            logger.warning("skipping a GPU with an empty name: %r", device)
            continue
        # ``GPUInfo.memory_gb`` has no ``ge=0`` constraint (unlike the node's
        # own ``cpus``/``memory_gb``), so a bogus negative would validate and
        # be stored unchallenged. Clamping is cheaper than a cross-cutting
        # schema change, which belongs to the metrics issue (#102).
        gpus.append(GPUInfo(name=name, memory_gb=max(memory_gb, 0)))
    return gpus


__all__ = [
    "BYTES_PER_GIB",
    "MINIMUM_CPUS",
    "MINIMUM_MEMORY_GB",
    "REGISTERED_STATUS",
    "collect_node_spec",
    "probe_hardware",
    "write_node_spec",
]
