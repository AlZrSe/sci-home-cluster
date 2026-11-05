"""Segment-based log writer for the file-only agent (issue #101)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.log_entry import LogEntry
from shared.schemas.log_segment import LogSegment

logger = logging.getLogger(__name__)

MAX_LINE_LENGTH = 10240
BUFFER_SIZE = 1000
FLUSH_LINES = 50
FLUSH_SECONDS = 5
MAX_SEGMENTS = 1000
ROTATE_LINES = 100
ROTATE_BYTES = 64 * 1024


class SegmentWriter:
    """Buffer log lines and flush them as create-only segment files."""

    def __init__(self, job_dir: Path, stream: str = "stdout") -> None:
        self.job_dir = Path(job_dir)
        self.stream = stream
        self._buffer: List[LogEntry] = []
        self._dropped = 0
        self._sequence = 0
        self._segments_written = 0

    def write_line(self, line: str, timestamp: Optional[datetime] = None) -> None:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        if len(line) > MAX_LINE_LENGTH:
            excess = len(line) - MAX_LINE_LENGTH
            suffix = f"...[truncated {excess} bytes]"
            line = line[: MAX_LINE_LENGTH - len(suffix)] + suffix
        entry = LogEntry(timestamp=timestamp, line=line, origin="real")
        if len(self._buffer) >= BUFFER_SIZE:
            # drop oldest
            self._buffer.pop(0)
            self._dropped += 1
            logger.warning("dropped log line; total dropped=%d", self._dropped)
        self._buffer.append(entry)

    def flush(self) -> Optional[Path]:
        if not self._buffer:
            return None
        if self._segments_written >= MAX_SEGMENTS:
            self._dropped += len(self._buffer)
            self._buffer.clear()
            logger.warning("segment cap reached; dropping")
            return None
        self._sequence += 1
        self._segments_written += 1
        seq_str = f"{self._sequence:04d}"
        logs_dir = self.job_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{seq_str}.yaml"
        path = logs_dir / filename
        segment = LogSegment(
            job_id=self.job_dir.name,
            stream=self.stream,  # type: ignore[arg-type]
            sequence=self._sequence,
            lines=list(self._buffer),
            dropped=self._dropped,
            created_at=datetime.now(timezone.utc),
        )
        write_yaml(str(path), segment)
        self._buffer.clear()
        return path

    def close(self) -> None:
        self.flush()
