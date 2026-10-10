"""The Syncthing folder layout, derived from settings rather than the environment."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from agent.paths import AgentPaths, resolve_paths
from shared.file_ops.path_utils import get_node_state_file

pytestmark = pytest.mark.unit


def test_paths_resolve_jobs_nodes_and_state_dir(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path, "node-01")
    assert paths.root == tmp_path
    assert paths.jobs_dir == tmp_path / "jobs"
    assert paths.nodes_dir == tmp_path / "nodes"
    assert paths.state_dir == tmp_path / ".agent" / "node-01"


def test_state_dir_is_outside_jobs_and_nodes(tmp_path: Path) -> None:
    """Neither watcher observes ``.agent/``, so neither sees agent state."""
    paths = resolve_paths(tmp_path, "node-01")
    assert paths.jobs_dir not in paths.state_dir.parents
    assert paths.nodes_dir not in paths.state_dir.parents
    assert paths.state_dir.name == "node-01"
    assert paths.state_dir.parent.name == ".agent"


def test_node_file_matches_shared_convention(tmp_path: Path, monkeypatch) -> None:
    paths = resolve_paths(tmp_path, "node-01")
    os.environ["SYNCTHING_ROOT"] = str(tmp_path)
    try:
        assert paths.node_file == get_node_state_file("node-01")
    finally:
        os.environ.pop("SYNCTHING_ROOT", None)


def test_paths_do_not_use_os_environ() -> None:
    source = (Path(__file__).resolve().parents[1] / "paths.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "os" not in imported


def test_paths_are_absolute_and_normalized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sync").mkdir()
    paths = resolve_paths("sync", "node-01")
    assert paths.root.is_absolute()
    assert paths.jobs_dir.is_absolute()
    assert paths.state_dir.is_absolute()
    assert ".." not in str(paths.root)


def test_resolve_paths_expands_user(tmp_path: Path, monkeypatch) -> None:
    """D4: a public path helper that raises on ``~`` is a trap for #97/#101/#102."""
    home = tmp_path / "home3"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    paths = resolve_paths("~/sync", "node-01")

    assert paths.root == home / "sync"
    assert paths.state_dir == home / "sync" / ".agent" / "node-01"


def test_resolve_paths_honours_an_explicit_state_dir(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path, "node-01", tmp_path / "elsewhere" / "state")
    assert paths.state_dir == tmp_path / "elsewhere" / "state"


def test_agent_paths_is_frozen(tmp_path: Path) -> None:
    paths: AgentPaths = resolve_paths(tmp_path, "node-01")
    with pytest.raises(Exception):
        paths.root = tmp_path  # type: ignore[misc]
