"""
Syncthing folder listing schemas.
"""

from pydantic import BaseModel, Field
from typing import List


class SyncthingFolderList(BaseModel):
    """List of directories under the Syncthing root."""

    root: str = Field(..., description="Absolute path to Syncthing root")
    folders: List[str] = Field(
        default_factory=list,
        description="Relative directory paths under the root, sorted",
    )

    model_config = {"from_attributes": True}
