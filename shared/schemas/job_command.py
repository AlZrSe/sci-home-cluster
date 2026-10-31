"""
Shared schemas for operator commands (issue #106).

A command is a single operator instruction written by the backend into the
Syncthing folder and consumed by the agent. The schema is closed: Phase 2
carries only ``cancel`` (see issue #106 Q5).
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

COMMAND_ID_PATTERN = r"^cancel-\d{10,}-[0-9a-f]{6}$"


class JobCommandType(str, Enum):
    """The only operator verb in Phase 2."""

    CANCEL = "cancel"


class JobCommand(BaseModel):
    """One operator instruction. Backend-owned, written once, never rewritten."""

    command_id: str = Field(..., pattern=COMMAND_ID_PATTERN)
    command: JobCommandType
    issued_at: datetime
    issued_by: str = Field(..., min_length=1, max_length=128)

    model_config = {"from_attributes": True}
