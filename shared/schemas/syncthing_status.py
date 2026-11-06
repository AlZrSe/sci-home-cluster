"""
Syncthing synchronization service status.
"""

from pydantic import BaseModel, Field


class GuiInfo(BaseModel):
    """Syncthing GUI information."""

    host: str
    port: int
    url: str
    reachable: bool


class FolderInfo(BaseModel):
    """Folder existence and file count information."""

    exists: bool
    path: str
    state_files: int = Field(..., ge=0)


class SyncthingStatus(BaseModel):
    """Syncthing synchronization service status."""

    running: bool
    root_path: str
    jobs_dir_exists: bool
    nodes_dir_exists: bool
    processed_files_count: int = Field(..., ge=0)
    jobs_folder: FolderInfo
    nodes_folder: FolderInfo
    gui: GuiInfo

    model_config = {"from_attributes": True}
