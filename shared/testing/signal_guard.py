"""Shared signal safety guard for pytest.

Extracted from agent/tests/conftest.py (issue #114) and promoted to a shared
module so the rootdir conftest.py can arm the guard before any testpath is
collected (issue #115).

Public exports:
- GUARDED_SIGNALS
- signal_name
- SignalSafetyGuard
- SIGNAL_GUARD (module-level singleton)
- pytest_configure
- pytest_unconfigure
- pytest_runtest_logreport
- pytest_runtest_protocol
- pytest_sessionfinish
"""

from __future__ import annotations

import signal
import sys
from typing import Any, Iterator

import pytest


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
    import sys

    print("pytest_configure called, installing guard", file=sys.stderr)
    SIGNAL_GUARD.install()
    print(f"Guard installed, original: {SIGNAL_GUARD.original}", file=sys.stderr)


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
    # Drain any signals that arrived when no test was running (e.g. during collection)
    stray = SIGNAL_GUARD.take_recorded()
    if stray:
        SIGNAL_GUARD.strays.append(("collection", list(stray)))

    if not SIGNAL_GUARD.strays:
        return
    print("\nissue #114 signal safety guard recorded:", file=sys.stderr)
    for nodeid, stray in SIGNAL_GUARD.strays:
        names = ", ".join(signal_name(s) for s in stray)
        if nodeid == "collection":
            print(
                f"a stray {names} reached the pytest process during collection",
                file=sys.stderr,
            )
        else:
            print(f"  {nodeid}: {names}", file=sys.stderr)
    if not exitstatus:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


__all__ = [
    "GUARDED_SIGNALS",
    "signal_name",
    "SignalSafetyGuard",
    "SIGNAL_GUARD",
    "pytest_configure",
    "pytest_unconfigure",
    "pytest_runtest_logreport",
    "pytest_runtest_protocol",
    "pytest_sessionfinish",
]
