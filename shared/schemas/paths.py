"""
Syncthing input/output paths for a job.
"""

from typing import Optional

from pydantic import BaseModel


class Paths(BaseModel):
    """Input and output paths for a job. Both are optional."""

    input: Optional[str] = None
    output: Optional[str] = None

    model_config = {"from_attributes": True}


# Backwards-compatible alias used by the original shared schema module.
JobPaths = Paths
