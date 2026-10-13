"""Node registration: the probe, the file, and the backend that reads it (#95).

Nothing in the repository wrote ``nodes/<node_id>.yaml`` before this, so these
tests are the first thing that ever puts a node into the cluster. Each one is
pinned to an acceptance criterion in ``docs/specs/95-node-registration-file.md``.

Two of them (AC-8, AC-9) drive the **real** backend consumer rather than a
stand-in for it -- ``SyncthingService._process_node_file`` and
``_heartbeat_monitor`` -- because the properties being asserted (create-then-
update is idempotent; a fresh heartbeat is not stale) live in that code. Only
the store behind it is a double, and :class:`FakeStore` reproduces the one
behaviour of the real one that matters to those tests, documented below.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import platform
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Sequence

import psutil
import pytest
import yaml
from pydantic import ValidationError

from agent import loop as agent_loop
from agent.config import EXIT_OK, build_settings
from agent.hardware import (
    BYTES_PER_GIB,
    MINIMUM_CPUS,
    MINIMUM_MEMORY_GB,
    collect_node_spec,
    probe_hardware,
    write_node_spec,
)
from agent.loop import FOLDER_WATCH_TASK, HEARTBEAT_INTERVAL_S, NODE_SPEC_TASK, Agent
from agent.paths import AgentPaths, resolve_paths
from agent.tests.conftest import RecordingWatcher, wait_until
from backend.services import syncthing_service
from backend.services.syncthing_service import SyncthingService
from shared.file_ops.path_utils import get_node_state_file
from shared.file_ops.yaml_utils import read_yaml
from shared.schemas.node_spec import GPUInfo, NodeSpec

pytestmark = pytest.mark.unit

HARDWARE_SOURCE = Path(__file__).resolve().parents[1] / "hardware.py"

#: A fixed instant, so a test can assert a whole ``NodeSpec`` exactly. Naive
#: local, because that is the convention the agent writes and the one the store
#: keeps -- see ``test_the_heartbeat_is_naive_local_because_the_store_is``.
NOW = datetime(2026, 10, 13, 12, 0)
LATER = NOW + timedelta(seconds=30)


def facts(**overrides: Any) -> dict[str, Any]:
    """The keyword arguments :func:`probe_hardware` takes, with overrides."""
    base: dict[str, Any] = {
        "node_id": "node-01",
        "hostname": "rig-01",
        "cpus": 16,
        "memory_bytes": 64 * BYTES_PER_GIB,
        "gpus": [],
        "os_name": "linux",
        "now": NOW,
    }
    return {**base, **overrides}


class FakeGPU:
    """The two attributes :func:`agent.hardware._gpu_infos` reads off a device."""

    def __init__(self, name: str = "NVIDIA RTX 4090", memory_total_mb: int = 24576):
        self.name = name
        self.memoryTotal = memory_total_mb


class ExplodingGPU:
    """A device whose attributes raise -- one broken card out of four."""

    @property
    def name(self) -> str:
        raise RuntimeError("the driver stopped answering")


class FakeGpuTil(ModuleType):
    """A stand-in for the ``gputil`` module, importable by name.

    Subclasses ``ModuleType`` rather than being a ``SimpleNamespace`` so that
    ``sys.modules["gputil"] = ...`` makes ``import gputil`` hand back exactly
    this object, which is the only way to exercise the *guarded* import without
    installing anything.
    """

    def __init__(self, devices: Sequence[Any] = (), error: Exception | None = None):
        super().__init__("gputil")
        self._devices = list(devices)
        self._error = error

    def getGPUs(self) -> list[Any]:  # gputil's own spelling, kept verbatim
        if self._error is not None:
            raise self._error
        return list(self._devices)


def fake_machine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hostname: str = "rig-01",
    cpus: int = 16,
    memory_bytes: int = 64 * BYTES_PER_GIB,
    system: str = "Linux",
) -> None:
    """Replace the four machine reads the collector makes."""
    monkeypatch.setattr(psutil, "cpu_count", lambda *_a, **_k: cpus)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(total=memory_bytes)
    )
    monkeypatch.setattr(platform, "node", lambda: hostname)
    monkeypatch.setattr(platform, "system", lambda: system)


def a_node_spec(node_id: str = "node-01", *, last_heartbeat: datetime) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        hostname="rig-01",
        gpus=[],
        cpus=8,
        memory_gb=32,
        os="linux",
        status="ONLINE",
        last_heartbeat=last_heartbeat,
        current_job_id=None,
    )


def fresh_paths(tmp_path: Path, node_id: str = "node-01") -> AgentPaths:
    """Paths under a replicated folder that the backend has never seen."""
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    return resolve_paths(root, node_id)


# --- AC-3: the probe, driven by fakes --------------------------------------


def test_probe_assembles_a_multi_gpu_node_exactly() -> None:
    gpus = [
        GPUInfo(name="NVIDIA RTX 4090", memory_gb=24),
        GPUInfo(name="NVIDIA RTX 3090", memory_gb=24),
    ]
    spec = probe_hardware(
        **facts(
            node_id="node-07",
            hostname="rig-07",
            cpus=32,
            memory_bytes=128 * BYTES_PER_GIB,
            gpus=gpus,
        )
    )
    assert spec.model_dump() == {
        "node_id": "node-07",
        "hostname": "rig-07",
        "gpus": [
            {"name": "NVIDIA RTX 4090", "memory_gb": 24},
            {"name": "NVIDIA RTX 3090", "memory_gb": 24},
        ],
        "cpus": 32,
        "memory_gb": 128,
        "os": "linux",
        "status": "ONLINE",
        "last_heartbeat": NOW,
        "current_job_id": None,
    }


def test_probe_assembles_a_cpu_only_node_exactly() -> None:
    # 3_970_000_000 bytes is this development box: 3.70 GiB, which truncates.
    spec = probe_hardware(
        **facts(cpus=4, memory_bytes=3_970_000_000, gpus=[], os_name="linux")
    )
    assert spec.model_dump() == {
        "node_id": "node-01",
        "hostname": "rig-01",
        "gpus": [],
        "cpus": 4,
        "memory_gb": 3,
        "os": "linux",
        "status": "ONLINE",
        "last_heartbeat": NOW,
        "current_job_id": None,
    }


def test_probe_floors_a_machine_that_reports_almost_nothing() -> None:
    spec = probe_hardware(**facts(cpus=0, memory_bytes=512 * 1024 * 1024))
    assert spec.cpus == MINIMUM_CPUS == 1
    assert spec.memory_gb == MINIMUM_MEMORY_GB == 1


def test_the_floor_is_load_bearing_rather_than_decoration() -> None:
    """Without it, ``ge=1`` rejects the node and the backend drops it silently."""
    floored = probe_hardware(**facts(cpus=0, memory_bytes=0))
    with pytest.raises(ValidationError):
        NodeSpec(**{**floored.model_dump(), "cpus": 0})
    with pytest.raises(ValidationError):
        NodeSpec(**{**floored.model_dump(), "memory_gb": 0})


def test_probe_never_invents_a_status_or_a_job() -> None:
    spec = probe_hardware(**facts())
    assert spec.status == "ONLINE", "the monitor owns the ONLINE->OFFLINE direction"
    assert spec.current_job_id is None, "#97 owns current_job_id"


def test_collect_reports_one_gpu_info_per_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "gputil",
        FakeGpuTil(
            [FakeGPU("NVIDIA RTX 4090", 24576), FakeGPU("NVIDIA RTX 3090", 24564)]
        ),
    )
    fake_machine(
        monkeypatch, hostname="rig-07", cpus=32, memory_bytes=128 * BYTES_PER_GIB
    )

    spec = collect_node_spec("node-01", now=NOW)

    # 24564 MiB truncates to 23 GiB, the same truncation the node's own memory
    # field gets: both fields are counted in GiB and neither is rounded.
    assert spec.gpus == [
        GPUInfo(name="NVIDIA RTX 4090", memory_gb=24),
        GPUInfo(name="NVIDIA RTX 3090", memory_gb=23),
    ]
    assert spec.model_dump() == {
        "node_id": "node-01",
        "hostname": "rig-07",
        "gpus": [
            {"name": "NVIDIA RTX 4090", "memory_gb": 24},
            {"name": "NVIDIA RTX 3090", "memory_gb": 23},
        ],
        "cpus": 32,
        "memory_gb": 128,
        "os": "linux",
        "status": "ONLINE",
        "last_heartbeat": NOW,
        "current_job_id": None,
    }


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux", "FreeBSD"])
def test_the_os_field_is_the_normalised_platform_name(
    monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    """Q2's decision, pinned off-Linux.

    On a Linux development box and on Linux CI, ``platform.system().lower()``
    *is* ``"linux"``, so a probe that simply returned the string ``"linux"``
    passed every test while being wrong on every other platform in the fleet.
    The decision being tested is that ``os`` is derived from the running
    platform and normalised, so it can only be observed by asking a platform
    that is not this one.
    """
    fake_machine(monkeypatch, system=system)

    spec = collect_node_spec("node-01", now=NOW)

    assert spec.os == system.lower()
    assert spec.os == spec.os.lower(), "os is normalised, not merely reported"


def test_collect_reports_no_gpus_when_gputil_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``None`` in sys.modules is how CPython reports "this import must fail",
    # which is the state of the development venv: pyproject declares gputil and
    # the venv does not have it.
    monkeypatch.setitem(sys.modules, "gputil", None)
    fake_machine(monkeypatch)

    spec = collect_node_spec("node-01", now=NOW)

    assert spec.gpus == []
    assert "gpus" in spec.model_dump(), "an omitted key fails validation, silently"


def test_collect_survives_gputil_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The certain case on a CPU-only box: ``getGPUs`` raises, not returns []."""
    monkeypatch.setitem(
        sys.modules,
        "gputil",
        FakeGpuTil(error=ValueError("Found no NVIDIA device on this system")),
    )
    fake_machine(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="agent.hardware"):
        spec = collect_node_spec("node-01", now=NOW)

    assert spec.gpus == []
    assert any("getGPUs() failed" in r.getMessage() for r in caplog.records)


def test_collect_keeps_the_gpus_that_read_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "gputil",
        FakeGpuTil([ExplodingGPU(), FakeGPU("NVIDIA RTX 4090", 24576)]),
    )
    fake_machine(monkeypatch)

    spec = collect_node_spec("node-01", now=NOW)

    assert [gpu.name for gpu in spec.gpus] == ["NVIDIA RTX 4090"]


def test_collect_drops_a_gpu_with_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GPUInfo.name`` has no constraint, so an empty one would be stored."""
    monkeypatch.setitem(
        sys.modules,
        "gputil",
        FakeGpuTil([FakeGPU("  ", 24576), FakeGPU("NVIDIA RTX 4090", 24576)]),
    )
    fake_machine(monkeypatch)

    spec = collect_node_spec("node-01", now=NOW)

    assert [gpu.name for gpu in spec.gpus] == ["NVIDIA RTX 4090"]


def test_collect_clamps_a_negative_gpu_memory_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GPUInfo.memory_gb`` has no ``ge=0``, unlike the node's own fields."""
    monkeypatch.setitem(
        sys.modules, "gputil", FakeGpuTil([FakeGPU("NVIDIA RTX 4090", -1)])
    )
    fake_machine(monkeypatch)

    spec = collect_node_spec("node-01", now=NOW)

    assert spec.gpus == [GPUInfo(name="NVIDIA RTX 4090", memory_gb=0)]


# --- AC-1 and AC-2: a real probe of this machine ---------------------------


def test_a_real_probe_populates_every_field() -> None:
    """AC-1: nothing is a literal except ``status`` and ``current_job_id``."""
    spec = collect_node_spec("node-01")

    assert spec.cpus >= 1 and spec.memory_gb >= 1
    assert spec.status == "ONLINE" and spec.current_job_id is None
    assert spec.last_heartbeat.tzinfo is None, (
        "naive local, like every other writer in this repository -- see "
        "test_the_heartbeat_is_naive_local_because_the_store_is"
    )
    assert spec.node_id == "node-01"
    assert spec.hostname == platform.node()
    assert spec.os == platform.system().lower()
    assert spec.cpus == max(int(psutil.cpu_count() or 0), 1)
    assert spec.memory_gb == max(psutil.virtual_memory().total // BYTES_PER_GIB, 1)


def test_the_heartbeat_is_naive_local_because_the_store_is() -> None:
    """The convention, asserted directly instead of inferred from a symptom.

    Two separate claims, and both matter:

    *naive* -- ``last_heartbeat`` is a SQLite ``DateTime`` column, so the store
    hands the value back without a timezone no matter what the agent writes.
    An aware value is therefore not extra information, it is information that
    gets dropped and then misread as local time.

    *local* -- the monitor subtracts it from a naive ``datetime.now()``, i.e.
    local. An aware-UTC value round-trips as naive **UTC**, so on any node whose
    clock is not UTC the delta is inflated by that node's offset and a healthy
    node is marked ``OFFLINE`` forever (#121).

    The first assertion holds everywhere; the second is what fails on the
    machines that are actually broken, which is why both are here rather than
    only the end-to-end symptom.
    """
    value = collect_node_spec("node-01").last_heartbeat

    assert value.tzinfo is None, "the store's column cannot keep an offset"

    drift = abs((datetime.now() - value).total_seconds())
    assert drift < 5, (
        f"the heartbeat is {drift:.0f}s away from naive local now; the agent is "
        "writing a clock that is not the one the monitor compares against"
    )


def test_the_gpus_key_is_present_on_this_machine() -> None:
    """AC-2: the key, not the value. This box has no GPU and must still write it."""
    spec = collect_node_spec("node-01")

    assert "gpus" in spec.model_dump(), "gpus key must be present"


def test_the_written_file_always_carries_the_gpus_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gputil", None)
    paths = fresh_paths(tmp_path)

    write_node_spec(paths, collect_node_spec("node-01", now=NOW))

    written = yaml.safe_load(paths.node_file.read_text(encoding="utf-8"))
    assert written["gpus"] == [], "an empty list, never an omitted key"


# --- AC-4: the round trip ---------------------------------------------------


def test_the_written_file_round_trips_through_read_yaml(tmp_path: Path) -> None:
    """One check that catches every schema constraint in the spec at once."""
    paths = fresh_paths(tmp_path)
    write_node_spec(paths, collect_node_spec("node-01"))

    reloaded = read_yaml(str(paths.node_file), NodeSpec)

    assert reloaded.node_id == "node-01"
    assert reloaded.status == "ONLINE"
    assert reloaded.current_job_id is None
    assert (
        reloaded.last_heartbeat.tzinfo is None
    ), "the value on disk is naive; YAML must not smuggle an offset back in"
    assert abs((datetime.now() - reloaded.last_heartbeat).total_seconds()) < 5


def test_a_gpu_node_round_trips_through_read_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "gputil",
        FakeGpuTil(
            [FakeGPU("NVIDIA RTX 4090", 24576), FakeGPU("NVIDIA RTX 3090", 24564)]
        ),
    )
    fake_machine(monkeypatch)
    paths = fresh_paths(tmp_path)

    write_node_spec(paths, collect_node_spec("node-01", now=NOW))

    assert read_yaml(str(paths.node_file), NodeSpec).gpus == [
        GPUInfo(name="NVIDIA RTX 4090", memory_gb=24),
        GPUInfo(name="NVIDIA RTX 3090", memory_gb=23),
    ]


# --- AC-5: the write is atomic ---------------------------------------------


#: Enough rewrites that a reader polling continuously is very likely to catch a
#: non-atomic write in the act. ``write_yaml`` writes a temp file and
#: ``os.replace``s it, so there is no window at all; an in-place
#: ``open(path, "w")`` leaves one, and this is the test that would catch it.
ATOMIC_WRITES = 40


def test_a_reader_never_sees_a_truncated_node_file(tmp_path: Path) -> None:
    paths = fresh_paths(tmp_path)
    specs = [
        a_node_spec(last_heartbeat=NOW + timedelta(seconds=index))
        for index in range(ATOMIC_WRITES)
    ]

    # The exact bytes of each document, computed once on a scratch folder. Two
    # identical specs serialise identically, so the concurrent phase below can be
    # compared against this set.
    scratch = resolve_paths(tmp_path / "scratch", "node-01")
    whole_documents: set[str] = set()
    for spec in specs:
        write_node_spec(scratch, spec)
        whole_documents.add(scratch.node_file.read_text(encoding="utf-8"))

    write_node_spec(paths, specs[0])  # steady state: the path always exists

    seen: list[str] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                seen.append(paths.node_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                seen.append(f"<unreadable: {exc!r}>")

    reader = threading.Thread(target=poll, name="node-file-reader", daemon=True)
    reader.start()
    try:
        for spec in specs[1:]:
            write_node_spec(paths, spec)
    finally:
        stop.set()
        reader.join(timeout=5.0)

    assert not reader.is_alive(), "the reader thread outlived the test"
    assert len(seen) >= 10, (
        f"the reader only sampled {len(seen)} times for {len(specs)} writes, so "
        "it cannot be evidence of anything"
    )
    assert len(set(seen)) > 1, "the reader never saw the file change"
    unexpected = sorted(set(seen) - whole_documents)
    assert unexpected == [], (
        "a reader saw a document that was never written whole; the write is not "
        f"atomic: {unexpected!r}"
    )


# --- AC-6: the path is the shared convention, not a hand-built string -------


def test_the_written_path_is_the_shared_convention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = fresh_paths(tmp_path)
    monkeypatch.setenv("SYNCTHING_ROOT", str(paths.root))

    written = write_node_spec(paths, collect_node_spec("node-01"))

    assert written == paths.node_file
    assert written == get_node_state_file("node-01"), (
        "the agent and the backend's watcher must resolve one file; the agent "
        "uses AgentPaths.node_file, which mirrors get_node_state_file"
    )


def test_the_only_path_written_is_the_one_the_paths_resolved() -> None:
    """Mechanical: nothing else in the module is allowed to build a path."""
    tree = ast.parse(HARDWARE_SOURCE.read_text(encoding="utf-8"))
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "write_yaml"
    ]
    assert len(writes) == 1, "one write, one place to audit"
    expected = ast.dump(ast.parse("str(paths.node_file)", mode="eval").body)
    assert ast.dump(writes[0].args[0]) == expected


# --- AC-7: the agent registers itself in a folder that does not exist yet ---


def agent_for(root: Path, node_id: str = "node-01", **env: str) -> Agent:
    settings = build_settings(
        ["--node-id", node_id, "--syncthing-root", str(root)], environ={**env}
    )
    paths = resolve_paths(
        settings.SYNCTHING_ROOT, settings.NODE_ID, settings.AGENT_STATE_DIR
    )
    return Agent(settings, watcher=RecordingWatcher(paths))


async def stop_and_join(agent: Agent, running: asyncio.Future[int]) -> int:
    supervisor = agent.supervisor
    assert supervisor is not None
    supervisor.stop_event.set()
    return await asyncio.wait_for(running, 5.0)


async def test_the_agent_registers_itself_in_an_empty_root(tmp_path: Path) -> None:
    """AC-7: a root the backend has never run against, not even created."""
    root = tmp_path / "brand-new-root"
    assert not root.exists()

    agent = agent_for(root)
    assert agent.paths.node_file == root.resolve() / "nodes" / "node-01.yaml"
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: agent.paths.node_file.exists(),
            "the agent never wrote its node file",
        )
        assert (root / "nodes").is_dir()
        assert read_yaml(str(agent.paths.node_file), NodeSpec).status == "ONLINE"
    finally:
        code = await stop_and_join(agent, running)

    assert code == EXIT_OK
    assert agent.supervisor is not None
    assert NODE_SPEC_TASK in agent.supervisor.task_names


async def test_registration_is_not_registered_when_the_folder_never_appears(
    tmp_path: Path,
) -> None:
    """A file that cannot be written must not be attempted every 30 s forever."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a regular file where a folder should be", encoding="utf-8")

    agent = agent_for(
        blocker,
        AGENT_FOLDER_RETRY_MAX_S="0.2",
        AGENT_FOLDER_WATCH_INTERVAL_S="0.05",
    )
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: agent.supervisor is not None
            and agent.supervisor.task_names == [FOLDER_WATCH_TASK],
            "the agent never settled into watching for a folder that is not there",
        )
        assert agent.supervisor is not None
        assert NODE_SPEC_TASK not in agent.supervisor.task_names
    finally:
        code = await stop_and_join(agent, running)

    assert code == EXIT_OK


# --- AC-8 and AC-9: the real backend consumer ------------------------------


class FakeStore:
    """The node half of ``DatabaseStore``, in memory.

    The real handler and the real monitor are the units under test; the store is
    the boundary they sit behind, and the agent suite stays off the database on
    purpose -- an agent test that opened the real store would be one refactor
    away from writing into the repository's ``.shc/``.

    One behaviour is reproduced exactly because the monitor depends on it:
    ``last_heartbeat`` goes into a SQLite ``DateTime`` column, which carries no
    timezone, so it comes back **naive** and the offset the writer supplied is
    simply gone. That loss is not a detail -- it is the whole reason the agent
    writes naive local time, and it is why a test that hands the monitor a
    hand-written ``datetime.now()`` proves nothing: that value already assumes
    away the bug. The monitor tests below therefore seed from what
    ``collect_node_spec`` actually produces.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, NodeSpec] = {}
        self.created: list[str] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    async def get_node(self, node_id: str) -> NodeSpec | None:
        return self.nodes.get(node_id)

    async def create_node(self, node_spec: NodeSpec) -> NodeSpec:
        if node_spec.node_id in self.nodes:
            raise ValueError(f"Node with ID {node_spec.node_id} already exists")
        self.nodes[node_spec.node_id] = _as_stored(node_spec)
        self.created.append(node_spec.node_id)
        return node_spec

    async def update_node(self, node_id: str, **kwargs: Any) -> NodeSpec | None:
        if node_id not in self.nodes:
            return None
        self.updated.append((node_id, kwargs))
        current = self.nodes[node_id]
        if "last_heartbeat" in kwargs:
            kwargs["last_heartbeat"] = _as_naive(kwargs["last_heartbeat"])
        self.nodes[node_id] = current.model_copy(update=kwargs)
        return self.nodes[node_id]

    async def list_nodes(self) -> list[NodeSpec]:
        return list(self.nodes.values())


def _as_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _as_stored(node_spec: NodeSpec) -> NodeSpec:
    return node_spec.model_copy(
        update={"last_heartbeat": _as_naive(node_spec.last_heartbeat)}
    )


@pytest.fixture
def backend_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Point the real service at an in-memory store for the duration of a test."""
    store = FakeStore()
    monkeypatch.setattr(syncthing_service, "get_store", lambda: store)
    return store


async def test_writing_twice_registers_exactly_one_node(
    tmp_path: Path, backend_store: FakeStore, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-8: create-then-update, driven through the real ``_process_node_file``."""
    paths = fresh_paths(tmp_path)
    service = SyncthingService(paths.root)

    with caplog.at_level(logging.ERROR, logger="backend.services.syncthing_service"):
        write_node_spec(paths, collect_node_spec("node-01", now=NOW))
        await service._process_node_file("node-01", str(paths.node_file))
        write_node_spec(paths, collect_node_spec("node-01", now=LATER))
        await service._process_node_file("node-01", str(paths.node_file))

    assert list(backend_store.nodes) == ["node-01"], "one node, not two"
    assert backend_store.created == ["node-01"], "the second write updates"
    assert [node_id for node_id, _ in backend_store.updated] == ["node-01"]
    assert backend_store.nodes["node-01"].last_heartbeat == LATER
    assert backend_store.nodes["node-01"].status == "ONLINE"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], f"the handler logs and drops; something was dropped: {errors}"


class InstantAsyncio:
    """``asyncio`` as the monitor sees it, with a 10 ms sleep instead of 30 s.

    ``SyncthingService._heartbeat_monitor`` sleeps 30 s between passes, which is
    the cadence under test and not something a test should wait for. Only the
    module reference the monitor reaches through is replaced, so the monitor's
    own code -- the staleness arithmetic, the ONLINE/OFFLINE transitions -- runs
    exactly as written.
    """

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0.01)
        # Recorded *after* the sleep, so one entry means one full pass completed.
        self.slept.append(seconds)

    #: The monitor's own ``except asyncio.CancelledError`` has to keep working.
    CancelledError = asyncio.CancelledError


async def drive_monitor(
    service: SyncthingService, monkeypatch: pytest.MonkeyPatch
) -> InstantAsyncio:
    clock = InstantAsyncio()
    monkeypatch.setattr(syncthing_service, "asyncio", clock)
    service._running = True
    monitor = asyncio.ensure_future(service._heartbeat_monitor())
    await wait_until(
        lambda: len(clock.slept) >= 1, "the heartbeat monitor never completed a pass"
    )
    service._running = False
    await asyncio.wait_for(monitor, 5.0)
    return clock


def heartbeat_as_the_agent_writes_it(age_s: float) -> datetime:
    """A heartbeat ``age_s`` old, built from what the agent actually produces.

    Every monitor test seeds through here. Hand-writing ``datetime.now()`` would
    assume the very thing under test: it is naive local, so it survives the
    store's timezone loss unscathed and the monitor reads it correctly whether or
    not the agent ever writes the right clock. Taking the value from
    ``collect_node_spec`` means a regression to an aware UTC heartbeat shows up
    as the symptom it causes in production -- a node marked ``OFFLINE`` on a box
    that is not on UTC -- instead of hiding behind a convenient fixture.
    """
    return collect_node_spec("node-01").last_heartbeat - timedelta(seconds=age_s)


async def seed_node(store: FakeStore, age_s: float) -> None:
    """Put a node in the store the way the real store would hold it.

    Through ``create_node``, never by assigning into ``store.nodes``: the SQLite
    ``DateTime`` column drops the offset on the way in, and a seed that skips that
    step hands the monitor a value the production database could not contain. An
    aware heartbeat then reaches the monitor's arithmetic intact, which raises
    ``TypeError`` inside its own ``except`` -- and the node stays ``ONLINE``
    because the monitor did nothing at all, rather than because it is healthy.
    """
    await store.create_node(
        a_node_spec("node-01", last_heartbeat=heartbeat_as_the_agent_writes_it(age_s))
    )


async def test_a_fresh_node_is_never_flipped_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_store: FakeStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-9: a 10 s heartbeat is well inside the monitor's 90 s threshold.

    The value under test is the agent's own, seeded through the store, so this
    goes red if ``collect_node_spec`` ever starts writing a tz-aware heartbeat
    again: the store drops the offset, the monitor reads UTC as local, and on a
    non-UTC node the delta is hours rather than seconds (#121).
    """
    await seed_node(backend_store, 10.0)
    service = SyncthingService(fresh_paths(tmp_path).root)

    with caplog.at_level(logging.ERROR, logger="backend.services.syncthing_service"):
        clock = await drive_monitor(service, monkeypatch)

    assert clock.slept and set(clock.slept) == {
        30.0
    }, "the monitor's own cadence is untouched: only the sleep length is"
    assert (
        backend_store.nodes["node-01"].status == "ONLINE"
    ), "a node whose heartbeat the agent just wrote must not be marked stale"
    assert backend_store.updated == [], "a healthy node needs no status write"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], (
        "the monitor logged and swallowed an error instead of comparing "
        f"heartbeats, so it left this node ONLINE for the wrong reason: {errors}"
    )


async def test_the_same_monitor_does_flip_a_stale_node_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_store: FakeStore
) -> None:
    """The control for the test above: without it, that one proves nothing."""
    await seed_node(backend_store, 120.0)
    service = SyncthingService(fresh_paths(tmp_path).root)

    await drive_monitor(service, monkeypatch)

    assert backend_store.nodes["node-01"].status == "OFFLINE"


def test_the_heartbeat_cadence_leaves_room_under_the_offline_threshold() -> None:
    """The coupling recorded in the spec's §4, asserted rather than assumed.

    ``SyncthingService._heartbeat_monitor`` hardcodes ``delta > 90``; #96 makes
    that configurable. 30 s buys two missed writes plus watcher latency before a
    healthy node is at risk.
    """
    assert HEARTBEAT_INTERVAL_S == 30.0
    assert 2 * HEARTBEAT_INTERVAL_S < 90, "one missed write must not be fatal"


# --- AC-10 and the cadence, through the running agent -----------------------


async def test_the_node_file_is_rewritten_on_every_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_loop, "HEARTBEAT_INTERVAL_S", 0.02)
    probes = 0

    def counting(node_id: str, *, now: datetime | None = None) -> NodeSpec:
        nonlocal probes
        probes += 1
        return a_node_spec(node_id, last_heartbeat=datetime.now())

    monkeypatch.setattr(agent_loop, "collect_node_spec", counting)

    agent = agent_for(tmp_path / "sync")
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: probes >= 3, "the registration task did not repeat on its interval"
        )
    finally:
        code = await stop_and_join(agent, running)

    assert code == EXIT_OK
    assert read_yaml(str(agent.paths.node_file), NodeSpec).status == "ONLINE"


async def test_a_bug_in_a_tick_does_not_stop_the_agent_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-10 again, for the other kind of failure.

    The ``OSError`` test above covers the transient case the tick handles
    itself. This one covers the class the tick deliberately does *not* handle:
    anything else is a bug, and it reaches the supervisor's containment. What
    must not change is the outcome -- the agent records the failure, keeps
    ticking, and exits 0. If the registration task were ever marked
    ``critical=True``, the same bug would route to ``_on_fatal`` and the agent
    would exit ``EXIT_INTERNAL_ERROR`` with every node it had ever had silently
    dropped from the dashboard.

    Asserting the consequence as well as the configuration is deliberate: the
    flag is data in a ``NamedTuple``, and this is what it buys.
    """
    monkeypatch.setattr(agent_loop, "HEARTBEAT_INTERVAL_S", 0.02)

    def explode(*_args: Any, **_kwargs: Any) -> NodeSpec:
        raise RuntimeError("the probe returned something impossible")

    monkeypatch.setattr(agent_loop, "collect_node_spec", explode)

    agent = agent_for(tmp_path / "sync")
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: agent.supervisor is not None
            and any(f.task_name == NODE_SPEC_TASK for f in agent.supervisor.failures),
            "the failing tick was never recorded by the supervisor",
        )
        assert not running.done(), "a bug in a tick must not stop the agent"
    finally:
        code = await stop_and_join(agent, running)

    assert code == EXIT_OK, (
        "a non-critical task records its failure and the agent still exits "
        f"cleanly; got {code}"
    )
    assert agent.supervisor is not None
    assert any(f.task_name == NODE_SPEC_TASK for f in agent.supervisor.failures)


async def test_a_failed_write_warns_and_the_agent_survives_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-10: a lost heartbeat costs a warning, never the process."""
    monkeypatch.setattr(agent_loop, "HEARTBEAT_INTERVAL_S", 0.02)
    real_write = write_node_spec
    attempts = 0

    def flaky(paths: AgentPaths, spec: NodeSpec) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(13, "Permission denied")
        return real_write(paths, spec)

    monkeypatch.setattr(agent_loop, "write_node_spec", flaky)

    agent = agent_for(tmp_path / "sync")
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: attempts >= 2, "the failed write was never retried on the next tick"
        )
        assert agent.paths.node_file.exists(), "the retry succeeded"
        assert not running.done(), "a lost heartbeat must not stop the agent"
    finally:
        code = await stop_and_join(agent, running)

    assert code == EXIT_OK
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "could not write the node file" in r.getMessage()
    ]
    assert len(warnings) == 1, warnings
    assert str(agent.paths.node_file) in warnings[0]
    assert agent.supervisor is not None
    assert (
        agent.supervisor.failures == []
    ), "an OSError is handled as a warning, not contained as a task failure"


async def test_the_agents_own_writes_do_not_come_back_as_events(
    tmp_path: Path,
) -> None:
    """Registration runs inside the watched folder; the ledger keeps it out."""
    agent = agent_for(tmp_path / "sync")
    running = asyncio.ensure_future(agent.run())
    try:
        await wait_until(
            lambda: agent.paths.node_file.exists(), "the agent never registered"
        )
    finally:
        await stop_and_join(agent, running)

    assert agent._watcher is not None
    assert agent._watcher.ledger.is_self_write(agent.paths.node_file)
