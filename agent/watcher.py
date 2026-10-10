"""
The folder watcher.

Reuses the two filters from ``backend/services/syncthing_service.py:53-62`` --
``.yaml`` suffix only, and skip dotfiles and ``.tmp`` -- and nothing else. The
backend's handler is ~100 lines because it also debounces and routes; this one
observes and enqueues, and parsing or routing is #97, #101 and #102.

Those two filters are load-bearing rather than decorative. They are what keeps
the agent's own ``write_yaml`` temp files (``<name>.<rand>.tmp``) out of the
event stream, and what keeps ``nodes/agent.toml`` invisible to both watchers.
The duplication is real and is recorded as issue #93 R5;
``test_agent_filter_matches_the_backend_filter`` is the tripwire that catches
one filter gaining a case the other does not.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from agent.paths import AgentPaths

logger = logging.getLogger(__name__)

EVENT_CREATED: Final[str] = "created"
EVENT_MODIFIED: Final[str] = "modified"
EVENT_DELETED: Final[str] = "deleted"

OBSERVER_JOIN_TIMEOUT_S: Final[float] = 5.0


class WatcherDead(RuntimeError):
    """The observer is not alive. Routed through the critical-task channel."""


@dataclass(frozen=True)
class FolderEvent:
    """One observed change. Observed only -- nothing here has been parsed."""

    kind: str
    path: Path
    observed_at: datetime


class SelfWriteLedger:
    """Bounded in-memory record of paths the agent wrote itself.

    Syncthing generally suppresses echoes of local changes, so this is belt and
    braces: it exists for the agent re-entering its own control loop on a file
    it wrote, not because echo suppression is assumed broken. It is in-memory
    and therefore **not** a durable guarantee -- after a restart the first event
    is always treated as observed.
    """

    MAX_REMEMBERED: Final[int] = 256

    def __init__(self, max_remembered: int = MAX_REMEMBERED) -> None:
        self._max_remembered = max_remembered
        self._entries: OrderedDict[Path, None] = OrderedDict()

    def record(self, path: Path) -> None:
        self._entries[path] = None
        self._entries.move_to_end(path)
        while len(self._entries) > self._max_remembered:
            self._entries.popitem(last=False)

    def is_self_write(self, path: Path) -> bool:
        return path in self._entries

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class _EventHandler(FileSystemEventHandler):
    """Turn watchdog callbacks into queued :class:`FolderEvent`s."""

    def __init__(
        self,
        enqueue: Callable[[FolderEvent], None],
        discard_pending: Callable[[], None],
        ledger: SelfWriteLedger,
    ) -> None:
        super().__init__()
        self._enqueue = enqueue
        self._discard_pending = discard_pending
        self._ledger = ledger

    @staticmethod
    def _is_relevant_file(path: str) -> bool:
        """Copy of ``SyncthingEventHandler._is_relevant_file``, filters only."""
        path_obj = Path(path)
        if path_obj.suffix.lower() != ".yaml":
            return False
        return not (path_obj.name.startswith(".") or path_obj.name.endswith(".tmp"))

    def _handle(self, kind: str, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if not self._is_relevant_file(str(path)):
            return
        if self._ledger.is_self_write(path):
            return
        self._enqueue(
            FolderEvent(kind=kind, path=path, observed_at=datetime.now(timezone.utc))
        )

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(EVENT_CREATED, event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(EVENT_MODIFIED, event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(EVENT_DELETED, event)

    def cancel_pending(self) -> None:
        """Drop events observed but not yet handed to the event loop.

        The backend cancels debounced futures for the same reason. Here the
        pending work is the batch the observer thread has not flushed yet; after
        the observer stops there is nothing left to flush it for.
        """
        self._discard_pending()


class FolderWatcher:
    """An in-process ``watchdog`` observer over ``jobs/`` and ``nodes/``."""

    def __init__(
        self,
        paths: AgentPaths,
        *,
        ledger: SelfWriteLedger | None = None,
        observer_factory: Callable[[], BaseObserver] = Observer,
        handler_class: type[_EventHandler] = _EventHandler,
    ) -> None:
        self._paths = paths
        self._ledger = ledger if ledger is not None else SelfWriteLedger()
        self._observer_factory = observer_factory
        self._handler_class = handler_class
        self._observer: BaseObserver | None = None
        self._handler: _EventHandler | None = None
        self._queue: asyncio.Queue[FolderEvent] = asyncio.Queue()
        self._pending: list[FolderEvent] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def ledger(self) -> SelfWriteLedger:
        return self._ledger

    def events(self) -> asyncio.Queue[FolderEvent]:
        return self._queue

    def start(self) -> None:
        for directory in (self._paths.jobs_dir, self._paths.nodes_dir):
            if not directory.is_dir():
                raise FileNotFoundError(
                    f"cannot watch {directory}: it does not exist. The watcher "
                    "is started only once the Syncthing folder is present."
                )

        loop = asyncio.get_running_loop()
        self._loop = loop

        handler = self._handler_class(
            self._enqueue, self._discard_pending, self._ledger
        )
        observer = self._observer_factory()
        observer.schedule(handler, str(self._paths.jobs_dir), recursive=True)
        observer.schedule(handler, str(self._paths.nodes_dir), recursive=True)
        observer.start()

        self._handler = handler
        self._observer = observer

    def stop(self) -> None:
        handler, self._handler = self._handler, None
        observer, self._observer = self._observer, None

        if handler is not None:
            handler.cancel_pending()
        if observer is not None:
            observer.stop()
            observer.join(timeout=OBSERVER_JOIN_TIMEOUT_S)
        self._loop = None

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def probe_liveness(self) -> None:
        """Raise :class:`WatcherDead` unless the observer thread is alive.

        Raises rather than returns ``False`` so that a dead watcher reuses the
        supervisor's existing ``critical=True`` -> ``on_fatal`` route instead of
        adding a second failure channel.

        This is a **local** thread/fd check. It catches a dead observer thread, a
        stopped emitter and a lost watch. It cannot catch a Syncthing *peer*
        dropping while the folder stays present and writable; that gap belongs
        in #104's documentation, not in a claim made here (issue #93 R8).
        """
        if not self.is_alive():
            raise WatcherDead(
                "the Syncthing folder watcher is not alive; the agent is no "
                "longer observing jobs/ or nodes/"
            )

    def _enqueue(self, event: FolderEvent) -> None:
        with self._lock:
            self._pending.append(event)
        self._schedule_flush()

    def _discard_pending(self) -> None:
        with self._lock:
            self._pending = []

    def _schedule_flush(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self._flush()
            return
        try:
            loop.call_soon_threadsafe(self._flush)
        except RuntimeError:
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            batch, self._pending = self._pending, []
        for event in batch:
            self._queue.put_nowait(event)
