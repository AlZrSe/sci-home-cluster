"""
Bounded line writer for capturing process stdout/stderr.

Provides a streaming writer that enforces a maximum line length and handles
undecodable bytes according to a documented policy (errors="replace").
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class BoundedLineWriter:
    """Write lines to a file with length cap and encoding policy.

    Each call to write_line decodes the input bytes with errors="replace",
    truncates to MAX_LINE_LENGTH, appends a newline if missing, and writes
    to the file. The file is opened in append mode for each write to avoid
    holding file handles open (which prevents deletion on Windows).
    """

    MAX_LINE_LENGTH: int = 10240  # 10 KiB

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path

    def write_line(self, line: bytes) -> None:
        """Decode with errors='replace', truncate to MAX_LINE_LENGTH, write."""
        # Ensure parent directory exists
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

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

        # Open, write, close - don't hold the handle
        with self._file_path.open("a", encoding="utf-8") as f:
            f.write(decoded)

    async def flush(self) -> None:
        """Ensure all buffered data is written. No-op since each write flushes."""
        await asyncio.sleep(0)  # Yield to event loop

    def close(self) -> None:
        """Close the underlying file. No-op since we don't hold handles."""
        pass

    def __enter__(self) -> "BoundedLineWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        """Ensure file handle is closed on garbage collection. No-op."""
        try:
            self.close()
        except Exception:
            # Ignore errors during finalization (e.g., interpreter shutdown)
            pass
