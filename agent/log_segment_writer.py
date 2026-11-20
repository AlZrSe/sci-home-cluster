"""Segment-based log writer for the file-only agent (issue #101)."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

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
    """Buffer log lines and flush them as create-only segment files.

    Flushes when buffer reaches FLUSH_LINES (50) OR FLUSH_SECONDS (5) elapses.
    Thread-safe: flush() can be called from timer thread while write_line()
    runs on reader thread.
    """

    def __init__(
        self, job_dir: Path, stream: str = "stdout", counter: Any = None
    ) -> None:
        self.job_dir = Path(job_dir)
        self.stream = stream
        self._buffer: List[LogEntry] = []
        self._dropped = 0
        self._sequence = 0
        self._segments_written = 0
        # Optional shared sequence counter (e.g. across stdout/stderr writers)
        # so segment filenames do not collide between streams.
        self._counter = counter
        # Thread safety for buffer access from timer thread and reader thread
        self._buffer_lock = threading.Lock()
        # Timer-based flush
        self._flush_timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()
        self._closed = False

    def write_line(self, line: Any, timestamp: Optional[datetime] = None) -> None:
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
        with self._buffer_lock:
            if len(self._buffer) >= BUFFER_SIZE:
                # drop oldest
                self._buffer.pop(0)
                self._dropped += 1
                logger.warning("dropped log line; total dropped=%d", self._dropped)
            self._buffer.append(entry)
        self._schedule_flush()
        if len(self._buffer) >= FLUSH_LINES:
            self.flush()

    def _schedule_flush(self) -> None:
        """Start or reset the flush timer."""
        with self._timer_lock:
            if self._closed:
                return
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(FLUSH_SECONDS, self._timer_flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _timer_flush(self) -> None:
        """Timer callback to flush the buffer."""
        # flush() is now thread-safe
        self.flush()

    def write_lines(self, lines: Any) -> Optional[Path]:
        for line in lines:
            self.write_line(line)
        return self.flush() if self._buffer else None

    def flush(self) -> Optional[Path]:
        # Take a snapshot of the buffer under the lock, then release it
        # while doing I/O.
        with self._buffer_lock:
            if not self._buffer:
                return None
            buffer_snapshot = list(self._buffer)
            self._buffer.clear()

        # Split the snapshot into rotation-sized chunks.
        chunks: List[List[LogEntry]] = []
        chunk: List[LogEntry] = []
        chunk_bytes = 0
        for entry in buffer_snapshot:
            entry_bytes = len(entry.line.encode("utf-8"))
            if chunk and (
                len(chunk) >= ROTATE_LINES or chunk_bytes + entry_bytes > ROTATE_BYTES
            ):
                chunks.append(chunk)
                chunk = []
                chunk_bytes = 0
            chunk.append(entry)
            chunk_bytes += entry_bytes
        if chunk:
            chunks.append(chunk)

        last_path: Optional[Path] = None
        for ch in chunks:
            if self._segments_written >= MAX_SEGMENTS:
                with self._buffer_lock:
                    self._dropped += len(ch)
                logger.warning("segment cap reached; dropping")
                continue
            if self._counter is not None:
                self._counter[0] += 1
                seq = self._counter[0]
            else:
                self._sequence += 1
                seq = self._sequence
            self._segments_written += 1
            seq_str = f"{seq:04d}"
            logs_dir = self.job_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            path = logs_dir / f"{seq_str}.yaml"
            segment = LogSegment(
                job_id=self.job_dir.name,
                stream=self.stream,  # type: ignore[arg-type]
                sequence=seq,
                lines=list(ch),
                dropped=self._dropped,
                created_at=datetime.now(timezone.utc),
            )
            write_yaml(str(path), segment)
            last_path = path

        return last_path

    def close(self) -> None:
        with self._timer_lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
        # Final flush (thread-safe)
        self.flush()
