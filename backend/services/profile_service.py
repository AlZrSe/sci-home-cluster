"""File-backed profile store (issue #91)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.profile import ProfileSpec

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


def profiles_dir() -> Path:
    return _PROFILES_DIR


def _ensure_dirs() -> None:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    (_PROFILES_DIR / "overrides").mkdir(parents=True, exist_ok=True)


def _hidden_file() -> Path:
    return _PROFILES_DIR / ".hidden.yaml"


def _load_hidden() -> set:
    p = _hidden_file()
    if not p.exists():
        return set()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict) and "hidden" in data:
            return set(data["hidden"])
    except Exception as e:
        logger.error("Failed to read hidden list %s: %s", p, e)
    return set()


def _save_hidden(hidden: set) -> None:
    import yaml

    _hidden_file().write_text(
        yaml.safe_dump(sorted(hidden), default_flow_style=False), encoding="utf-8"
    )


def _is_builtin_path(p: Path) -> bool:
    return (
        p.parent == _PROFILES_DIR
        and p.suffix.lower() == ".yaml"
        and not p.name.startswith(".")
    )


def _load_profile_file(p: Path) -> Optional[ProfileSpec]:
    try:
        return read_yaml(str(p), ProfileSpec)
    except Exception as e:
        logger.error("Failed to load profile file %s: %s", p, e)
        return None


def list_profiles() -> List[ProfileSpec]:
    _ensure_dirs()
    hidden = _load_hidden()
    result: List[ProfileSpec] = []
    # builtins
    for p in sorted(_PROFILES_DIR.glob("*.yaml")):
        if p.name.startswith("."):
            continue
        prof = _load_profile_file(p)
        if prof is None:
            continue
        if prof.id in hidden:
            continue
        # overlay?
        overlay = _PROFILES_DIR / "overrides" / f"{prof.id}.yaml"
        if overlay.exists():
            o = _load_profile_file(overlay)
            if o is not None:
                prof = o
        result.append(prof)
    # Custom profiles stored as <id>.yaml in root.
    return result


def profile_file_path(profile_id: str) -> Path:
    return _PROFILES_DIR / f"{profile_id}.yaml"


def get_profile(profile_id: str) -> Optional[ProfileSpec]:
    for p in list_profiles():
        if p.id == profile_id:
            return p
    # try overlay-only custom
    overlay = _PROFILES_DIR / "overrides" / f"{profile_id}.yaml"
    if overlay.exists():
        return _load_profile_file(overlay)
    return None


def create_profile(profile: ProfileSpec) -> ProfileSpec:
    _ensure_dirs()
    profile.builtin = False
    path = _PROFILES_DIR / f"{profile.id}.yaml"
    write_yaml(str(path), profile)
    return profile


def update_profile(profile_id: str, profile: ProfileSpec) -> Optional[ProfileSpec]:
    _ensure_dirs()
    existing = get_profile(profile_id)
    if existing is None:
        return None
    profile.id = profile_id
    if existing.builtin:
        overlay = _PROFILES_DIR / "overrides" / f"{profile_id}.yaml"
        write_yaml(str(overlay), profile)
    else:
        path = _PROFILES_DIR / f"{profile_id}.yaml"
        write_yaml(str(path), profile)
    return profile


def delete_profile(profile_id: str) -> bool:
    _ensure_dirs()
    existing = get_profile(profile_id)
    if existing is None:
        return False
    if existing.builtin:
        hidden = _load_hidden()
        hidden.add(profile_id)
        _save_hidden(hidden)
        # remove overlay if any
        overlay = _PROFILES_DIR / "overrides" / f"{profile_id}.yaml"
        if overlay.exists():
            overlay.unlink()
        return True
    path = _PROFILES_DIR / f"{profile_id}.yaml"
    if path.exists():
        path.unlink()
    return True


def reset_profile(profile_id: str) -> bool:
    _ensure_dirs()
    hidden = _load_hidden()
    hidden.discard(profile_id)
    _save_hidden(hidden)
    overlay = _PROFILES_DIR / "overrides" / f"{profile_id}.yaml"
    if overlay.exists():
        overlay.unlink()
    return True


def restore_builtins() -> None:
    _ensure_dirs()
    _save_hidden(set())
    overrides = _PROFILES_DIR / "overrides"
    if overrides.exists():
        for p in overrides.glob("*.yaml"):
            p.unlink()
