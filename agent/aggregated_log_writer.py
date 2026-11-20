"""Aggregated log writer for human-readable single-file per stream."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.log_entry import LogEntry
from shared.schemas.log_segment import LogSegment

logger = logging.getLogger(__name__)

MAX_LINE_LENGTH = 10240


class AggregatedWriter:
    """Append log lines to a single YAML file per stream.

    Unlike SegmentWriter which rotates create-only segment files,
    this writes to a single persistent file that accumulates all lines.
    """

    def __init__(self, job_dir: Path, stream: str = "stdout") -> None:
        self.job_dir = Path(job_dir)
        self.stream = stream
        self._path = self.job_dir / "logs" / f"{stream}.yaml"
        self._segments_written = 0
        self._lines: list[LogEntry] = []

    def write_line(self, line: Any, timestamp: datetime | None = None) -> None:
        if isinstance(line, BaseException):
            line = f"{type(line).__name__}: {line}"
        elif isinstance(line, (bytes, bytearray)):
            line = bytes(line).decode("utf-8", errors="replace")
        else:
            line = str(line)
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        if len(line) > MAX_LINE_LENGTH:
            excess = len(line) - MAX_LINE_LENGTH
            suffix = f"...[truncated {excess} bytes]"
            line = line[: MAX_LINE_LENGTH - len(suffix)] + suffix
        entry = LogEntry(timestamp=timestamp, line=line, origin="real")
        self._lines.append(entry)

    def flush(self) -> None:
        if not self._lines:
            return
        # Write the accumulated lines as a single LogSegment with all lines
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._segments_written += 1
        segment = LogSegment(
            job_id=self.job_dir.name,
            stream=self.stream,  # type: ignore[arg-type]
            sequence=self._segments_written,
            lines=list(self._lines),
            dropped=0,
            created_at=datetime.now(timezone.utc),
        )
        write_yaml(str(self._path), segment)

    def close(self) -> None:
        self.flush()
