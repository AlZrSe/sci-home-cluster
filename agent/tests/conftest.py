"""Fixtures for the agent suite.

Two of these exist to stop the suite from contaminating itself:
``restore_process_environment`` undoes the deliberate one-way ``SYNCTHING_ROOT``
publication that ``build_settings`` performs, and ``preserve_root_logging``
restores the root logger's handlers around the tests that call
``configure_logging``. Without them every test after the first would inherit the
first test's state (issue #93 US-5, R2).

A third exists because a test failure is not the worst thing this suite can do
to itself: the agent half of it used to signal *pytest*, and a signal delivered
at ``SIG_DFL`` kills the interpreter mid-file with no traceback and no summary
(issue #114). ``SignalSafetyGuard`` turns that back into a red test.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from watchdog.observers import Observer

from agent.config import build_settings
from agent.paths import AgentPaths, resolve_paths
from agent.watcher import FolderEvent, FolderWatcher

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- issue #114: the signal safety guard ------------------------------------
#
# Layer 2 of the guard described in docs/specs/114-agent-tests-kill-pytest.md.
# Layer 1 is the disposition assertion immediately before every ``os.kill`` in
# test_loop.py; this layer covers the window *between* tests, which is fatal for
# the same reason and which no assertion at the delivery site can reach.

#: The signals the guard watches. ``SIGQUIT`` is excluded because catching it
#: suppresses core dumps, and ``SIGHUP`` is not what #114 is about -- widening
#: this list is a judgement call for QA, not something this issue needs.
GUARDED_SIGNALS: tuple[int, ...] = (int(signal.SIGTERM), int(signal.SIGINT))


def signal_name(signum: int) -> str:
    """A printable name for ``signum``, which need not be a known signal."""
    try:
        return signal.Signals(signum).name
    except ValueError:  # pragma: no cover - no known signal carries this number
        return f"signal {signum}"


class _ExpectedSignal:
    """The context manager :meth:`SignalSafetyGuard.expecting` hands back."""

    def __init__(self, guard: SignalSafetyGuard, signum: int) -> None:
        self._guard = guard
        self._signum = signum

    def __enter__(self) -> None:
        self._guard.expected.append(self._signum)

    def __exit__(self, *_exc: Any) -> None:
        # The expectation deliberately outlives the block: delivery goes
        # through asyncio's self-pipe and lands on a later loop iteration, so
        # whichever handler ends up seeing the signal may run after it was
        # sent. It is consumed by the next take_recorded().
        return None


class SignalSafetyGuard:
    """Makes a stray signal a red test instead of a dead pytest process.

    The handler installed here records and returns. Recording is the whole
    point: re-raising the default action would reproduce exactly the bug this
    class exists to prevent, and pytest cannot run *any* finaliser once the
    interpreter is gone -- so a signal that kills the process destroys the
    evidence with it. Here the signal is attributed to the test that was
    running, the run finishes, and the failure is in the summary.

    Deliberately narrow: it does not ignore signals. It records them and fails
    the test, which is the difference between a red test and a hole.
    """

    def __init__(self) -> None:
        self.original: dict[int, Any] = {}
        self.strays: list[tuple[str, list[int]]] = []
        self.caught: list[int] = []
        self.expected: list[int] = []

    # -- installation -------------------------------------------------

    def install(self) -> None:
        """Arm the recording handler for every guarded signal. Idempotent.

        Only ever *adds* a Python-level handler. The dispositions this session
        found are recorded once, on the first call, so :meth:`restore` can put
        them back even after dozens of re-arms.
        """
        for signum in GUARDED_SIGNALS:
            if signum not in self.original:
                self.original[signum] = signal.getsignal(signum)
            signal.signal(signum, self.record)

    def restore(self) -> None:
        """Put back the dispositions this session found."""
        for signum, handler in list(self.original.items()):
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError, TypeError):  # pragma: no cover
                pass
        self.original.clear()

    # -- recording ----------------------------------------------------

    def record(self, signum: int, _frame: Any = None) -> None:
        """The installed handler: append, and deliberately do not re-raise."""
        self.caught.append(int(signum))

    def expecting(self, signum: int) -> _ExpectedSignal:
        """Declare ``signum`` deliberately delivered by the running test."""
        return _ExpectedSignal(self, int(signum))

    def take_recorded(self) -> list[int]:
        """Signals that arrived unasked for; clears both buffers."""
        outstanding = list(self.expected)
        stray: list[int] = []
        for signum in self.caught:
            if signum in outstanding:
                outstanding.remove(signum)
            else:
                stray.append(signum)
        self.caught.clear()
        self.expected.clear()
        return stray


#: The one guard for the session. Module-level so the hooks, the fixture and
#: the delivery helper in test_loop.py all refer to the same object.
SIGNAL_GUARD = SignalSafetyGuard()


def pytest_configure(config: pytest.Config) -> None:
    """Arm the guard before anything can leave the process at SIG_DFL."""
    SIGNAL_GUARD.install()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Leave the process exactly as this session found it."""
    SIGNAL_GUARD.restore()


def _stray_signal_message(nodeid: str, stray: list[int]) -> str:
    names = ", ".join(signal_name(s) for s in stray)
    return (
        f"a stray {names} reached the pytest process during {nodeid}, and "
        "nothing in that test asked for it. The signal safety guard records "
        "signals instead of dying on them, so the run can report them; at "
        "SIG_DFL the default action would have killed the whole session "
        "(issue #114)."
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: Any) -> None:
    """Turn a recorded stray signal into a failure on the test it landed in.

    ``tryfirst`` puts this ahead of the terminal reporter, which decides the
    reported outcome and the progress character from this same object -- so the
    failure reaches the short summary instead of scrolling past as a dot.

    Drained on the teardown report, which is the last thing the test owns.
    """
    if report.when != "teardown":
        return
    stray = SIGNAL_GUARD.take_recorded()
    if not stray:
        return
    SIGNAL_GUARD.strays.append((report.nodeid, list(stray)))
    report.outcome = "failed"
    report.longrepr = _stray_signal_message(report.nodeid, stray)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Iterator[None]:
    """Arm the guard across a whole test, including the gap between tests.

    pytest-asyncio closes each test's event loop, and a closed
    ``_UnixSelectorEventLoop`` removes the signal handlers it installed, so
    between one test's teardown and the next one's setup the process is back
    at ``SIG_DFL`` and a stray signal there is fatal with nothing to report.
    A fixture cannot close that window -- its finaliser runs *before* the loop
    is closed -- so the guard is re-armed here as well (issue #114 D4/R1).
    """
    SIGNAL_GUARD.install()
    yield
    SIGNAL_GUARD.install()

    # Backstop only. A signal that lands after the teardown report was built
    # can no longer be attributed to a report, so it is reported by
    # pytest_sessionfinish instead of by the test it landed in.
    stray = SIGNAL_GUARD.take_recorded()
    if stray:
        SIGNAL_GUARD.strays.append((item.nodeid, list(stray)))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Report the evidence, and make sure a recorded signal costs a non-zero exit.

    Editing the teardown report is not enough on its own: pytest counts a
    failure when the report is *first published*, which is before this hook
    runs, so without the correction below the summary would show a red test and
    the process would still exit 0 -- which is the failure mode this issue is
    about, one level up.
    """
    if not SIGNAL_GUARD.strays:
        return
    print("\nissue #114 signal safety guard recorded:", file=sys.stderr)
    for nodeid, stray in SIGNAL_GUARD.strays:
        print(
            f"  {nodeid}: {', '.join(signal_name(s) for s in stray)}", file=sys.stderr
        )
    if not exitstatus:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


class FakeObserver:
    """Stands in for ``watchdog.observers.Observer``; opens no inotify fd."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, object]] = []
        self.started = False
        self.stopped = False
        self.joined = False
        self.alive = False
        self.start_error: BaseException | None = None

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        self.scheduled.append((path, handler))
        return object()

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.joined = True

    def kill_emitter(self) -> None:
        """Simulate the emitter thread dying under a still-running observer."""
        self.alive = False


class RecordingWatcher(FolderWatcher):
    """A ``FolderWatcher`` whose queue the test drives by hand."""

    def __init__(self, paths: AgentPaths) -> None:
        super().__init__(paths, observer_factory=FakeObserver)  # type: ignore[arg-type]

    def emit(self, kind: str, path: Path) -> None:
        self.events().put_nowait(FolderEvent(kind=kind, path=path, observed_at=_NOW()))


def _NOW() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def signal_safety_guard() -> Iterator[SignalSafetyGuard]:
    """Re-arm the signal safety guard for every test (issue #114 D4).

    ``Agent.run()`` installs its own SIGTERM/SIGINT handlers mid-test and never
    removes them, so a guard armed once at session start would be gone by the
    time the second signal test finished. Re-arming per test means a stray
    signal is recorded again from the next boundary on.
    """
    SIGNAL_GUARD.install()
    yield SIGNAL_GUARD


@pytest.fixture(autouse=True)
def restore_process_environment() -> Iterator[None]:
    """Undo ``build_settings``' one-way publication after every test."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def preserve_root_logging() -> Iterator[None]:
    """Restore the root logger's handlers, level and record factory."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    factory = logging.getLogRecordFactory()
    try:
        yield
    finally:
        root.handlers = handlers
        root.setLevel(level)
        logging.setLogRecordFactory(factory)


@pytest.fixture
def syncthing_root(tmp_path: Path) -> Path:
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    return root


@pytest.fixture
def agent_settings(syncthing_root: Path):
    return build_settings(
        ["--node-id", "node-test", "--syncthing-root", str(syncthing_root)],
        environ={},
    )


@pytest.fixture
def tmp_paths(syncthing_root: Path) -> AgentPaths:
    return resolve_paths(syncthing_root, "node-test")


@pytest.fixture
def fake_observer() -> list[FakeObserver]:
    """Every ``FakeObserver`` the watcher under test constructed."""
    return []


@pytest.fixture
def observer_factory(fake_observer: list[FakeObserver]) -> Callable[[], Any]:
    def build() -> Any:
        observer = FakeObserver()
        fake_observer.append(observer)
        return observer

    return build


@pytest.fixture
def watcher(
    tmp_paths: AgentPaths, observer_factory: Callable[[], Any]
) -> RecordingWatcher:
    instance = RecordingWatcher(tmp_paths)
    instance._observer_factory = observer_factory  # type: ignore[attr-defined]
    return instance


@pytest.fixture
def scripted_stop() -> asyncio.Event:
    return asyncio.Event()


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Poll ``predicate`` on the running loop until it is true or time runs out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


#: Generous ceiling for :func:`wait_until`. Long enough that a loaded machine
#: misses the deadline only when the property is genuinely false, short enough
#: that a real failure is reported in seconds rather than minutes.
WAIT_UNTIL_TIMEOUT_S: float = 10.0


async def wait_until(
    predicate: Callable[[], bool],
    message: str,
    *,
    timeout: float = WAIT_UNTIL_TIMEOUT_S,
) -> None:
    """Block until ``predicate`` holds; fail the test with ``message`` if it never does.

    The load-safe replacement for "sleep a fixed fraction of a second, then
    assert that something happened ``n`` times". A fixed sleep and a count
    assertion are two separate bets: the sleep must outlast the interval *and*
    the event loop must get enough scheduling slots to deliver the ticks. Under
    load a 10 ms-interval task can be starved for the whole window, and the
    count assertion then fails for a supervisor that is behaving correctly.

    Waiting for the property itself removes the first bet. The caller keeps its
    own assertion on the same property afterwards, so a genuine failure is still
    reported as an assertion failure and not swallowed by the wait.
    """
    assert await wait_for(predicate, timeout), message


def real_watch_supported() -> bool:
    """Whether this platform can run a real ``watchdog`` observer at all.

    Windows uses ``ReadDirectoryChangesW`` and Linux ``inotify``; a few CI
    sandboxes support neither, and those tests skip rather than fail.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        observer = Observer()
        try:
            observer.schedule(lambda *_a: None, tmp, recursive=True)
            observer.start()
        except (OSError, NotImplementedError, RuntimeError):
            return False
        observer.stop()
        observer.join(timeout=5.0)
    return True


REAL_WATCH_SUPPORTED = real_watch_supported()

__all__ = [
    "GUARDED_SIGNALS",
    "REPO_ROOT",
    "SIGNAL_GUARD",
    "WAIT_UNTIL_TIMEOUT_S",
    "FakeObserver",
    "RecordingWatcher",
    "REAL_WATCH_SUPPORTED",
    "SignalSafetyGuard",
    "wait_for",
    "wait_until",
    "sys",
]
