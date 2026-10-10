"""
Filesystem layout of the Syncthing shared folder, as the agent sees it.

Every path is derived from the settings value rather than from
``shared.file_ops.path_utils.get_*_directory()``: those helpers read
``os.environ["SYNCTHING_ROOT"]`` with no injection point, which would make the
agent's path model depend on process-wide state that tests cannot control.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.file_ops.path_utils import normalize_path

#: Suffix of the per-node state file, matching ``get_node_state_file`` in
#: ``shared/file_ops/path_utils.py``. The agent does not write it in #93; the
#: field exists so #95 does not re-derive the convention.
NODE_FILE_SUFFIX = ".yaml"


@dataclass(frozen=True)
class AgentPaths:
    """Every directory the agent is allowed to look at, resolved once."""

    root: Path
    jobs_dir: Path
    nodes_dir: Path
    state_dir: Path
    node_file: Path


def resolve_root(value: str | Path) -> Path:
    """Expand ``~`` and make ``value`` absolute.

    ``shared.file_ops.path_utils.normalize_path`` subscripts a ``Path`` for any
    ``~/...`` input and raises ``TypeError`` (issue #93 D6/C16), so the tilde is
    expanded by ``pathlib`` first. That bug is out of scope here; the agent
    simply does not depend on it.

    Resolution is idempotent, which is what lets ``build_settings`` resolve the
    root *before* deriving the config path and still hand the resolved value to
    the settings model.
    """
    return normalize_path(Path(value).expanduser())


def resolve_paths(
    root: str | Path,
    node_id: str,
    state_dir: str | Path | None = None,
) -> AgentPaths:
    """Build the folder layout for one node under ``root``.

    ``~`` is expanded here rather than relying on the caller, because this is a
    public helper: #97, #101 and #102 all call it, and a path helper that
    raises on the most common way to write a home directory is a trap (issue #93
    D4).
    """
    resolved_root = resolve_root(root)
    nodes_dir = resolved_root / "nodes"
    return AgentPaths(
        root=resolved_root,
        jobs_dir=resolved_root / "jobs",
        nodes_dir=nodes_dir,
        state_dir=resolve_root(state_dir)
        if state_dir is not None
        else resolved_root / ".agent" / node_id,
        node_file=nodes_dir / f"{node_id}{NODE_FILE_SUFFIX}",
    )
