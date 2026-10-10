"""Configuration precedence, validation, and the shared-file contract."""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.config import (
    CONFIG_FILE_NAME,
    NODE_ID_PATTERN,
    AgentSettings,
    ConfigError,
    build_settings,
    config_file_path,
)
from backend.services.syncthing_service import SyncthingEventHandler

pytestmark = pytest.mark.unit


def build(environ: dict[str, str], *argv: str) -> AgentSettings:
    """``build_settings`` with an explicit argv.

    ``build_settings`` defaults ``argv`` to ``sys.argv``, which is right for
    ``run-agent`` and wrong for a test; every call here names its own.
    """
    return build_settings(list(argv), environ=environ)


def write_config(root: Path, body: str) -> Path:
    nodes = root / "nodes"
    nodes.mkdir(parents=True, exist_ok=True)
    path = nodes / CONFIG_FILE_NAME
    path.write_text(body, encoding="utf-8")
    return path


def base_env(root: Path) -> dict[str, str]:
    return {"NODE_ID": "node-01", "SYNCTHING_ROOT": str(root)}


# --- defaults -------------------------------------------------------------


def test_defaults_when_nothing_configured(syncthing_root: Path) -> None:
    settings = build(base_env(syncthing_root))
    assert settings.NODE_ID == "node-01"
    assert settings.LOG_LEVEL == "INFO"
    assert settings.SHUTDOWN_GRACE_S == 30.0
    assert settings.FOLDER_WATCH_INTERVAL_S == 30.0
    assert settings.FOLDER_RETRY_MAX_S == 60.0
    assert settings.WATCHER_LIVENESS_INTERVAL_S == 60.0


def test_missing_node_id_is_rejected_with_actionable_message(
    syncthing_root: Path,
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        build({"SYNCTHING_ROOT": str(syncthing_root)})
    message = str(excinfo.value)
    assert "--node-id" in message
    assert "NODE_ID" in message
    assert "replicated" in message


def test_node_id_from_env(syncthing_root: Path) -> None:
    assert build(base_env(syncthing_root)).NODE_ID == "node-01"


# --- precedence -----------------------------------------------------------


def test_cli_overrides_env(syncthing_root: Path) -> None:
    assert (
        build(base_env(syncthing_root), "--node-id", "from-cli").NODE_ID == "from-cli"
    )


def test_cli_log_level_wins_over_everything(syncthing_root: Path) -> None:
    write_config(syncthing_root, 'AGENT_LOG_LEVEL = "WARNING"\n')
    settings = build(
        {**base_env(syncthing_root), "AGENT_LOG_LEVEL": "CRITICAL"},
        "--log-level",
        "ERROR",
    )
    assert settings.LOG_LEVEL == "ERROR"


def test_unset_optional_flag_is_not_treated_as_a_value(syncthing_root: Path) -> None:
    """An absent optional flag must not overwrite a lower layer with ``None``."""
    write_config(syncthing_root, 'AGENT_LOG_LEVEL = "WARNING"\n')
    assert build(base_env(syncthing_root)).LOG_LEVEL == "WARNING"


def test_toml_used_when_env_unset(syncthing_root: Path) -> None:
    write_config(
        syncthing_root,
        'AGENT_LOG_LEVEL = "WARNING"\nAGENT_SHUTDOWN_GRACE_S = 1.5\n',
    )
    settings = build(base_env(syncthing_root))
    assert settings.LOG_LEVEL == "WARNING"
    assert settings.SHUTDOWN_GRACE_S == 1.5


def test_env_overrides_toml_file(
    syncthing_root: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    """R1: the environment must beat the shared file.

    Extended past the obvious absolute-root case to a ``~`` root and a
    relative root, because the lost implementation derived the config path from
    the *unresolved* root and so skipped the file entirely for those two forms
    (issue #93 D1). The spec's original version of this test used an absolute
    root only and would not have caught it.
    """
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    for case_root in (home / "syncthing", workdir / "syncthing"):
        (case_root / "nodes").mkdir(parents=True)
        (case_root / "nodes" / CONFIG_FILE_NAME).write_text(
            'AGENT_LOG_LEVEL = "WARNING"\n', encoding="utf-8"
        )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    write_config(syncthing_root, 'AGENT_LOG_LEVEL = "WARNING"\n')

    roots = [
        ("absolute", str(syncthing_root)),
        ("tilde", "~/syncthing"),
    ]

    for label, root in roots:
        from_file = build({**base_env(syncthing_root), "SYNCTHING_ROOT": root})
        assert from_file.LOG_LEVEL == "WARNING", label

        from_env = build(
            {
                **base_env(syncthing_root),
                "SYNCTHING_ROOT": root,
                "AGENT_LOG_LEVEL": "DEBUG",
            }
        )
        assert from_env.LOG_LEVEL == "DEBUG", label

    monkeypatch.chdir(workdir)
    from_file = build({"NODE_ID": "node-01", "SYNCTHING_ROOT": "syncthing"})
    assert from_file.LOG_LEVEL == "WARNING", "relative"
    from_env = build(
        {
            "NODE_ID": "node-01",
            "SYNCTHING_ROOT": "syncthing",
            "AGENT_LOG_LEVEL": "DEBUG",
        }
    )
    assert from_env.LOG_LEVEL == "DEBUG", "relative"


def test_config_file_is_found_for_a_relative_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workdir = tmp_path / "work"
    (workdir / "syncthing" / "nodes").mkdir(parents=True)
    (workdir / "syncthing" / "nodes" / CONFIG_FILE_NAME).write_text(
        'AGENT_LOG_LEVEL = "WARNING"\n', encoding="utf-8"
    )
    monkeypatch.chdir(workdir)

    settings = build({"NODE_ID": "node-01", "SYNCTHING_ROOT": "syncthing"})

    assert settings.LOG_LEVEL == "WARNING"
    assert settings.SYNCTHING_ROOT == (workdir / "syncthing").resolve()


def test_derived_config_file_is_read_without_an_explicit_path(
    syncthing_root: Path,
) -> None:
    write_config(syncthing_root, "AGENT_FOLDER_WATCH_INTERVAL_S = 7.5\n")
    settings = build(base_env(syncthing_root))
    assert settings.FOLDER_WATCH_INTERVAL_S == 7.5
    assert config_file_path(settings.SYNCTHING_ROOT) == (
        syncthing_root / "nodes" / CONFIG_FILE_NAME
    )


# --- D1: the config path is derived from the RESOLVED root -----------------


def test_config_file_is_found_for_a_tilde_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = tmp_path / "home"
    (home / "syncthing" / "nodes").mkdir(parents=True)
    (home / "syncthing" / "nodes" / CONFIG_FILE_NAME).write_text(
        'AGENT_LOG_LEVEL = "WARNING"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    settings = build({"NODE_ID": "node-01", "SYNCTHING_ROOT": "~/syncthing"})

    assert settings.SYNCTHING_ROOT == home / "syncthing"
    assert (
        settings.LOG_LEVEL == "WARNING"
    ), "a tilde root silently skipped the shared config file (issue #93 D1)"


# --- the .toml suffix is load-bearing (VM-2) ------------------------------


def test_config_file_toml_suffix_is_ignored_by_the_backend_watcher(
    syncthing_root: Path,
) -> None:
    """The suffix is what stops a phantom node (issue #93 Q1 Edge 1).

    Asserted against the backend's **live** filter, not against a string
    literal, so renaming the constant cannot make this pass.
    """
    handler = SyncthingEventHandler(cast(Any, SimpleNamespace()))
    derived = config_file_path(syncthing_root)

    assert derived.name == CONFIG_FILE_NAME
    assert handler._is_relevant_file(str(derived)) is False
    assert handler._is_relevant_file(str(derived.with_suffix(".yaml"))) is True


# --- the shared file's error surface --------------------------------------


def test_absent_toml_is_not_an_error(syncthing_root: Path) -> None:
    assert not (syncthing_root / "nodes" / CONFIG_FILE_NAME).exists()
    assert build(base_env(syncthing_root)).LOG_LEVEL == "INFO"


def test_malformed_toml_raises_with_the_path(syncthing_root: Path) -> None:
    path = write_config(syncthing_root, "AGENT_LOG_LEVEL = \n")
    with pytest.raises(ConfigError) as excinfo:
        build(base_env(syncthing_root))
    assert str(path) in str(excinfo.value)


def test_unknown_toml_key_is_rejected(syncthing_root: Path) -> None:
    write_config(syncthing_root, 'AGENT_LOG_LEVL = "WARNING"\n')
    with pytest.raises(ConfigError) as excinfo:
        build(base_env(syncthing_root))
    assert "AGENT_LOG_LEVL" in str(excinfo.value)


def test_toml_that_is_not_a_table_is_rejected(syncthing_root: Path) -> None:
    write_config(syncthing_root, "AGENT_LOG_LEVEL = 3\n[agent.node-01]\nx = 1\n")
    with pytest.raises(ConfigError) as excinfo:
        build(base_env(syncthing_root))
    assert "table" in str(excinfo.value)


def test_node_id_in_toml_is_rejected(syncthing_root: Path) -> None:
    """Every node reads the same replicated file (issue #93 Q1 Edge 2, VM-3)."""
    write_config(syncthing_root, 'NODE_ID = "node-01"\n')
    with pytest.raises(ConfigError) as excinfo:
        build(base_env(syncthing_root))
    message = str(excinfo.value)
    assert "NODE_ID" in message
    assert str(syncthing_root / "nodes" / CONFIG_FILE_NAME) in message
    assert "replicated" in message


def test_syncthing_root_in_toml_is_rejected_with_a_dedicated_message(
    syncthing_root: Path,
) -> None:
    """NHD-3 option A: its own message, not the generic unknown-key rejection."""
    write_config(syncthing_root, 'SYNCTHING_ROOT = "/elsewhere"\n')
    with pytest.raises(ConfigError) as excinfo:
        build(base_env(syncthing_root))
    message = str(excinfo.value)
    assert "SYNCTHING_ROOT" in message
    assert "locates this file" in message
    assert "--syncthing-root" in message
    assert "unknown key" not in message


# --- field validation -----------------------------------------------------


@pytest.mark.parametrize("bad", ["../other", "a/b", "", "..", ".hidden", "node 01"])
def test_node_id_rejects_path_separators(syncthing_root: Path, bad: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        build({"SYNCTHING_ROOT": str(syncthing_root)}, "--node-id", bad)
    message = str(excinfo.value)
    assert "NODE_ID" in message
    assert NODE_ID_PATTERN in message


def test_node_id_pattern_matches_documented_regex() -> None:
    compiled = re.compile(NODE_ID_PATTERN)
    for good in ("n", "node-01", "Node_01", "a" * 64):
        assert compiled.match(good), good
    for bad in ("", "-node", "_node", "a" * 65, "node 01", "node.01", "../x"):
        assert not compiled.match(bad), bad


def test_unknown_log_level_is_rejected_listing_valid_levels(
    syncthing_root: Path,
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        build({**base_env(syncthing_root), "AGENT_LOG_LEVEL": "LOUD"})
    message = str(excinfo.value)
    assert "LOUD" in message
    for level in ("INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"):
        assert level in message


def test_syncthing_root_has_no_default() -> None:
    with pytest.raises(ConfigError) as excinfo:
        build({"NODE_ID": "node-01"})
    message = str(excinfo.value)
    assert "SYNCTHING_ROOT" in message
    assert "/tmp/syncthing" not in message


def test_syncthing_root_expands_user(tmp_path: Path, monkeypatch: Any) -> None:
    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    settings = build({"NODE_ID": "node-01", "SYNCTHING_ROOT": "~/sync"})
    assert settings.SYNCTHING_ROOT == home / "sync"


def test_negative_intervals_rejected(syncthing_root: Path) -> None:
    with pytest.raises(ConfigError):
        build({**base_env(syncthing_root), "AGENT_SHUTDOWN_GRACE_S": "-1"})


def test_build_settings_does_not_read_process_environment(
    syncthing_root: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("AGENT_LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("AGENT_SHUTDOWN_GRACE_S", "999")
    settings = build(base_env(syncthing_root))
    assert settings.LOG_LEVEL == "INFO"
    assert settings.SHUTDOWN_GRACE_S == 30.0


# --- US-8: no credentials -------------------------------------------------


def test_agent_settings_model_has_no_credential_field() -> None:
    banned = ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL")
    for name in AgentSettings.model_fields:
        assert not any(word in name.upper() for word in banned), name


def test_a_credential_in_the_environment_changes_nothing(syncthing_root: Path) -> None:
    plain = build(base_env(syncthing_root))
    hostile = build(
        {
            **base_env(syncthing_root),
            "SHARED_TOKEN": "secret-value",
            "SECRET_KEY": "other",
            "JWT_SECRET": "third",
        }
    )
    assert plain.model_dump() == hostile.model_dump()


# --- VM-4: the one-way os.environ publication -----------------------------


def test_build_settings_publishes_syncthing_root_to_os_environ(
    syncthing_root: Path,
) -> None:
    os.environ.pop("SYNCTHING_ROOT", None)
    settings = build(base_env(syncthing_root))
    assert os.environ["SYNCTHING_ROOT"] == str(settings.SYNCTHING_ROOT)


def test_build_settings_publishes_only_the_root(syncthing_root: Path) -> None:
    """Exactly one key is touched -- added, or re-set if it was already there."""
    before = dict(os.environ)
    settings = build(base_env(syncthing_root))
    changed = {
        key
        for key in set(before) | set(os.environ)
        if os.environ.get(key) != before.get(key)
    }
    assert changed == {"SYNCTHING_ROOT"}
    assert os.environ["SYNCTHING_ROOT"] == str(settings.SYNCTHING_ROOT)


def test_published_root_is_not_read_back_in(syncthing_root: Path) -> None:
    """Publication is one-way: it must never become a precedence input."""
    build(base_env(syncthing_root))
    assert os.environ["SYNCTHING_ROOT"] == str(syncthing_root)

    with pytest.raises(ConfigError):
        build({"NODE_ID": "node-02"})
