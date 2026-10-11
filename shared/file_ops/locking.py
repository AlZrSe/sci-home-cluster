"""
Cross-platform file locking mechanism for the Scientific Home Cluster.
Supports Windows, Linux, and macOS using portalocker with fallbacks.
"""

import os
import sys
import logging
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

# Try to import portalocker (preferred cross-platform solution)
try:
    import portalocker
    from portalocker.exceptions import BaseLockException

    PORTALOCKER_AVAILABLE = True
except ImportError:
    PORTALOCKER_AVAILABLE = False
    BaseLockException = None  # type: ignore[assignment,misc]
    logger.warning("portalocker not available, using platform-specific fallbacks")

# Platform-specific fallback imports
if sys.platform.startswith("win"):
    try:
        import msvcrt

        HAS_MSVCRT = True
    except ImportError:
        HAS_MSVCRT = False
else:
    try:
        import fcntl

        HAS_FCNTL = True
    except ImportError:
        HAS_FCNTL = False


def _portalocker_flags(shared: bool) -> int:
    """Portalocker flags for a non-blocking shared or exclusive lock."""
    if shared:
        return portalocker.LOCK_SH | portalocker.LOCK_NB
    return portalocker.LOCK_EX | portalocker.LOCK_NB


def _fcntl_flags(shared: bool) -> int:
    """fcntl flags for a non-blocking shared or exclusive flock.

    Only reached when portalocker is unavailable, so ``fcntl`` is imported
    by the platform-specific fallback above.
    """
    if shared:
        return fcntl.LOCK_SH | fcntl.LOCK_NB
    return fcntl.LOCK_EX | fcntl.LOCK_NB


def _acquire(f, shared: bool, timeout: float) -> bool:
    """
    Try to acquire the lock on an open file, retrying until timeout expires.

    Returns True as soon as the lock is taken, False if the deadline passed
    without ever acquiring it. Callers must not unlock a False result: the
    descriptor was never locked by this caller.
    """
    import time

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            if PORTALOCKER_AVAILABLE:
                # Use portalocker (preferred)
                portalocker.lock(f, _portalocker_flags(shared))
                return True
            elif sys.platform.startswith("win") and HAS_MSVCRT:
                # Windows fallback using msvcrt. msvcrt has no native shared
                # lock, so it takes an exclusive lock for both modes.
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            elif not sys.platform.startswith("win") and HAS_FCNTL:
                # Unix fallback using fcntl
                fcntl.flock(f.fileno(), _fcntl_flags(shared))
                return True
        except (OSError, IOError, BaseLockException):
            # Lock not available, wait and retry
            time.sleep(0.1)
            continue

    return False


def _release(f) -> None:
    """Release the lock held on an open file, using the same backend as _acquire."""
    if PORTALOCKER_AVAILABLE:
        portalocker.unlock(f)
    elif sys.platform.startswith("win") and HAS_MSVCRT:
        # Reset file pointer to beginning and unlock
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    elif not sys.platform.startswith("win") and HAS_FCNTL:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def _lock(
    file_path: str,
    mode: str,
    timeout: float,
    shared: bool,
    label: str,
) -> Generator:
    """
    Shared core behind file_lock and shared_lock.

    Args:
        file_path: Path to the file to lock
        mode: File open mode
        timeout: Maximum time to wait for lock in seconds
        shared: Request a shared (read) lock rather than an exclusive one
        label: "lock" or "shared lock"; only ever appears in message text,
            so that the two public wrappers keep their historical wording

    Yields:
        file object: The opened and locked file object
    """
    # Ensure directory exists
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    # Open file
    try:
        f = open(file_path, mode)
    except IOError as e:
        logger.error(f"Failed to open file {file_path}: {e}")
        raise

    lock_acquired = False
    try:
        # Try to acquire lock with timeout
        lock_acquired = _acquire(f, shared, timeout)

        if not lock_acquired:
            f.close()
            raise TimeoutError(
                f"Could not acquire {label} on {file_path} after {timeout} seconds"
            )

        # Lock acquired, yield file object
        yield f

    finally:
        # Release lock and close file. lock_acquired guards the release: a
        # caller that timed out never locked this descriptor, and unlocking
        # it could drop a lock held by someone else.
        try:
            if lock_acquired:
                _release(f)
        except Exception as e:
            logger.warning(f"Error releasing {label} on {file_path}: {e}")
        finally:
            f.close()


@contextmanager
def file_lock(file_path: str, mode: str = "r", timeout: float = 10.0) -> Generator:
    """
    Cross-platform file locking context manager.

    Args:
        file_path: Path to the file to lock
        mode: File open mode ('r' for read, 'w' for write, etc.)
        timeout: Maximum time to wait for lock in seconds

    Yields:
        file object: The opened and locked file object

    Raises:
        TimeoutError: If lock cannot be acquired within timeout
        IOError: If file cannot be opened
    """
    with _lock(file_path, mode, timeout, shared=False, label="lock") as f:
        yield f


@contextmanager
def shared_lock(file_path: str, mode: str = "r", timeout: float = 10.0) -> Generator:
    """
    Cross-platform shared (read) file locking context manager.

    Args:
        file_path: Path to the file to lock
        mode: File open mode ('r' for read, etc.)
        timeout: Maximum time to wait for lock in seconds

    Yields:
        file object: The opened and locked file object

    Raises:
        TimeoutError: If lock cannot be acquired within timeout
        IOError: If file cannot be opened
    """
    with _lock(file_path, mode, timeout, shared=True, label="shared lock") as f:
        yield f
