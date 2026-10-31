"""
Shared schemas for command acknowledgements (issue #106).

An ack is the agent's receipt for one command: it records whether the command
was honoured, refused, or already handled.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .job_command import COMMAND_ID_PATTERN, JobCommandType


class JobCommandResult(str, Enum):
    """Outcome of handling one command."""

    TERMINATED = "terminated"
    ALREADY_TERMINAL = "already_terminal"
    IGNORED_NOT_OWNER = "ignored_not_owner"
    IGNORED_NO_JOB = "ignored_no_job"
    FAILED = "failed"


class JobCommandAck(BaseModel):
    """The agent's receipt for one command. Agent-owned, written once."""

    command_id: str = Field(..., pattern=COMMAND_ID_PATTERN)
    command: JobCommandType
    result: JobCommandResult
    acknowledged_at: datetime
    acknowledged_by: str = Field(..., min_length=1, max_length=128)
    detail: Optional[str] = Field(default=None, max_length=512)

    model_config = {"from_attributes": True}
