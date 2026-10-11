"""
Regression tests for the locking de-duplication in shared/file_ops (issue #54).

`file_lock` and `shared_lock` were two ~82-line near-copies that differed only
in the lock mode, which tripped ruff's C901 complexity gate (14 > 10). They now
delegate to a single private core (`_lock`), so the behaviour the duplication
used to provide implicitly now has to be pinned explicitly.

The load-bearing one is `lock_acquired`: a caller whose acquire loop times out
must never release a lock it does not hold. A prototype of the refactor dropped
that flag, so these tests check it by observing the calls, not by reading the
diff.

This is a separate module from `test_shared_file_ops.py` because these tests
monkeypatch `portalocker.lock` / `portalocker.unlock` and hold real locks
across threads for hundreds of milliseconds. Keeping them apart means the
monkeypatching and the timing sensitivity do not leak into the YAML
round-trip/model tests, and the guard regression stays easy to find.
"""

import logging
import os
import sys
import tempfile
import threading
from contextlib import contextmanager

import portalocker
import pytest

from shared.file_ops import locking
from shared.file_ops.locking import file_lock, shared_lock

# The contention tests depend on POSIX flock refusing a second opener; they
# are skipped on Windows, matching the existing test_lock_timeout.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="timing-based lock contention test"
)


@contextmanager
def held_lock(path, mode="w"):
    """Hold an exclusive ``file_lock`` on ``path`` in a background thread."""
    acquired = threading.Event()
    finish = threading.Event()
    failures = []

    def hold():
        try:
            with file_lock(path, mode=mode, timeout=10.0):
                acquired.set()
                finish.wait(10)
        except Exception as exc:  # pragma: no cover - re-raised by the assert
            failures.append(exc)
            acquired.set()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert acquired.wait(10), "background thread never acquired the lock"
    try:
        yield
    finally:
        finish.set()
        thread.join(10)
    assert not thread.is_alive(), "background lock holder never finished"
    assert not failures, f"background lock holder raised: {failures}"


@posix_only
def test_contention_timeout_does_not_release_the_holders_lock():
    """A timed-out acquire must leave the existing holder's lock intact."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "contention.lock")

        with held_lock(path):
            with pytest.raises(TimeoutError):
                with file_lock(path, mode="w", timeout=0.3):
                    pass

            # The holder never left its `with` block, so it still owns the
            # lock: a third party must still be refused.
            with pytest.raises(TimeoutError):
                with file_lock(path, mode="w", timeout=0.3):
                    pass


@posix_only
def test_timeout_never_reaches_the_release_path(monkeypatch, caplog):
    """
    The `lock_acquired` guard itself.

    A timed-out acquire closed its descriptor and never locked it, so the
    release branch must be skipped entirely. This is the assertion that fails
    if the guard is dropped: the release call then happens, and because the
    descriptor is already closed it raises and logs a spurious
    "Error releasing ..." warning.
    """
    unlocked = []
    real_unlock = portalocker.unlock

    def spy_unlock(fh):
        unlocked.append(fh)
        return real_unlock(fh)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "guard.lock")

        with held_lock(path):
            with monkeypatch.context() as m:
                m.setattr(locking.portalocker, "unlock", spy_unlock)
                with caplog.at_level(logging.WARNING, logger=locking.logger.name):
                    with pytest.raises(TimeoutError):
                        with file_lock(path, mode="w", timeout=0.3):
                            pass

            assert unlocked == [], "timed-out acquire called portalocker.unlock"
            release_warnings = [
                r.getMessage()
                for r in caplog.records
                if "Error releasing" in r.getMessage()
            ]
            assert release_warnings == []


def test_lock_modes_reach_portalocker(monkeypatch):
    """`file_lock` must ask for LOCK_EX, `shared_lock` for LOCK_SH."""
    seen = []
    real_lock = portalocker.lock

    def spy_lock(fh, flags):
        seen.append(flags)
        return real_lock(fh, flags)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "modes.lock")

        with monkeypatch.context() as m:
            m.setattr(locking.portalocker, "lock", spy_lock)

            with file_lock(path, mode="w", timeout=2.0):
                pass
            exclusive = seen[-1]

            with shared_lock(path, mode="r", timeout=2.0):
                pass
            shared = seen[-1]

    assert exclusive & portalocker.LOCK_EX
    assert not exclusive & portalocker.LOCK_SH
    assert shared & portalocker.LOCK_SH
    assert not shared & portalocker.LOCK_EX
    assert exclusive & portalocker.LOCK_NB
    assert shared & portalocker.LOCK_NB


@pytest.mark.parametrize(
    ("lock", "expected"),
    [
        (file_lock, "Could not acquire lock on"),
        (shared_lock, "Could not acquire shared lock on"),
    ],
)
@posix_only
def test_timeout_message_text_is_unchanged(lock, expected):
    """Both TimeoutError messages must stay byte-identical to `main`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "message.lock")

        with held_lock(path):
            with pytest.raises(TimeoutError) as exc_info:
                with lock(path, mode="r", timeout=0.3):
                    pass

    assert str(exc_info.value) == f"{expected} {path} after 0.3 seconds"


@pytest.mark.parametrize(
    ("lock", "expected_label"),
    [(file_lock, "lock"), (shared_lock, "shared lock")],
)
def test_release_failure_warning_text_is_unchanged(
    monkeypatch, caplog, lock, expected_label
):
    """Both release warnings must keep their historical wording."""

    def boom(fh):
        raise RuntimeError("kaboom")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "release-warning.lock")

        with monkeypatch.context() as m:
            m.setattr(locking, "_release", boom)
            with caplog.at_level(logging.WARNING, logger=locking.logger.name):
                with lock(path, mode="w", timeout=2.0):
                    pass

    messages = [r.getMessage() for r in caplog.records if r.name == locking.logger.name]
    assert f"Error releasing {expected_label} on {path}: kaboom" in messages


@pytest.mark.parametrize(
    ("lock", "defaults"),
    [(file_lock, ("r", 10.0)), (shared_lock, ("r", 10.0))],
)
def test_public_signature_is_unchanged(lock, defaults):
    """Callers depend on the positional (file_path, mode, timeout) shape."""
    import inspect

    signature = inspect.signature(lock)
    assert list(signature.parameters) == ["file_path", "mode", "timeout"]
    assert signature.parameters["mode"].default == defaults[0]
    assert signature.parameters["timeout"].default == defaults[1]
    assert signature.parameters["file_path"].default is inspect.Parameter.empty
