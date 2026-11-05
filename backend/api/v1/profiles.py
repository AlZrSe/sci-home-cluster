"""Profile management API endpoints (issue #91)."""

from typing import List

from fastapi import APIRouter, Depends

from backend.core.errors import profile_invalid, profile_not_found

from backend.core.deps import get_current_token_payload
from backend.services.profile_service import (
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    profile_file_path,
    reset_profile,
    restore_builtins,
    update_profile,
)
from shared.file_ops.yaml_utils import read_yaml
from shared.schemas.profile import ProfileSpec

router = APIRouter()


@router.get("/", response_model=List[ProfileSpec])
async def list_all(payload: dict = Depends(get_current_token_payload)):
    return list_profiles()


@router.get("/{profile_id}", response_model=ProfileSpec)
async def get_one(profile_id: str, payload: dict = Depends(get_current_token_payload)):
    path = profile_file_path(profile_id)
    if path.exists():
        try:
            base = read_yaml(str(path), ProfileSpec)
        except Exception as e:
            raise profile_invalid(str(path), str(e))
        # Apply overlay for built-ins (D1) when present.
        overlay = path.parent / "overrides" / f"{profile_id}.yaml"
        if overlay.exists():
            try:
                return read_yaml(str(overlay), ProfileSpec)
            except Exception as e:
                raise profile_invalid(str(overlay), str(e))
        return base
    prof = get_profile(profile_id)
    if prof is None:
        raise profile_not_found(profile_id)
    return prof


@router.post("/", response_model=ProfileSpec, status_code=201)
async def create(
    profile: ProfileSpec,
    payload: dict = Depends(get_current_token_payload),
):
    return create_profile(profile)


@router.put("/{profile_id}", response_model=ProfileSpec)
async def update(
    profile_id: str,
    profile: ProfileSpec,
    payload: dict = Depends(get_current_token_payload),
):
    result = update_profile(profile_id, profile)
    if result is None:
        raise profile_not_found(profile_id)
    return result


@router.delete("/{profile_id}", status_code=204)
async def delete(profile_id: str, payload: dict = Depends(get_current_token_payload)):
    ok = delete_profile(profile_id)
    if not ok:
        raise profile_not_found(profile_id)
    return None


@router.post("/{profile_id}/reset", response_model=ProfileSpec)
async def reset(profile_id: str, payload: dict = Depends(get_current_token_payload)):
    reset_profile(profile_id)
    prof = get_profile(profile_id)
    if prof is None:
        raise profile_not_found(profile_id)
    return prof


@router.post("/restore-builtins", status_code=204)
async def restore(payload: dict = Depends(get_current_token_payload)):
    restore_builtins()
    return None
