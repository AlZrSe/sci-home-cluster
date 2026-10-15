"""
Bounded line writer for capturing process stdout/stderr.

Provides a streaming writer that enforces a maximum line length and handles
undecodable bytes according to a documented policy (errors="replace").
"""

import asyncio
import logging
from pathlib import Path
from typing import IO, Optional

logger = logging.getLogger(__name__)


class BoundedLineWriter:
    """Write lines to a file with length cap and encoding policy.

    Each call to write_line decodes the input bytes with errors="replace",
    truncates to MAX_LINE_LENGTH, appends a newline if missing, and writes
    to the file. The file is opened in append mode.
    """

    MAX_LINE_LENGTH: int = 10240  # 10 KiB

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file: Optional[IO[str]] = None

    def _ensure_open(self) -> None:
        if self._file is None:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._file_path.open("a", encoding="utf-8")

    def write_line(self, line: bytes) -> None:
        """Decode with errors='replace', truncate to MAX_LINE_LENGTH, write."""
        self._ensure_open()

        # Decode with replacement for undecodable bytes
        decoded = line.decode("utf-8", errors="replace")

        # Ensure newline termination
        if not decoded.endswith("\n"):
            decoded += "\n"

        # Truncate if too long
        if len(decoded) > self.MAX_LINE_LENGTH:
            excess = len(decoded) - self.MAX_LINE_LENGTH
            decoded = decoded[: self.MAX_LINE_LENGTH - 1] + "\n"
            logger.debug(
                "Truncated log line by %d bytes for %s", excess, self._file_path
            )

        assert self._file is not None
        self._file.write(decoded)
        self._file.flush()

    async def flush(self) -> None:
        """Ensure all buffered data is written."""
        if self._file is not None:
            self._file.flush()
            await asyncio.sleep(0)  # Yield to event loop

    def close(self) -> None:
        """Close the underlying file."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "BoundedLineWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
