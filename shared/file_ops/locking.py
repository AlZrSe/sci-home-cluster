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
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if PORTALOCKER_AVAILABLE:
                    # Use portalocker (preferred)
                    portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    lock_acquired = True
                    break
                elif sys.platform.startswith("win") and HAS_MSVCRT:
                    # Windows fallback using msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    lock_acquired = True
                    break
                elif not sys.platform.startswith("win") and HAS_FCNTL:
                    # Unix fallback using fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_acquired = True
                    break
            except (OSError, IOError, BaseLockException):
                # Lock not available, wait and retry
                time.sleep(0.1)
                continue

        if not lock_acquired:
            f.close()
            raise TimeoutError(
                f"Could not acquire lock on {file_path} after {timeout} seconds"
            )

        # Lock acquired, yield file object
        yield f

    finally:
        # Release lock and close file
        try:
            if lock_acquired:
                if PORTALOCKER_AVAILABLE:
                    portalocker.unlock(f)
                elif sys.platform.startswith("win") and HAS_MSVCRT:
                    # Reset file pointer to beginning and unlock
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                elif not sys.platform.startswith("win") and HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"Error releasing lock on {file_path}: {e}")
        finally:
            f.close()


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
        # Try to acquire shared lock with timeout
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if PORTALOCKER_AVAILABLE:
                    # Use portalocker for shared lock
                    portalocker.lock(f, portalocker.LOCK_SH | portalocker.LOCK_NB)
                    lock_acquired = True
                    break
                elif sys.platform.startswith("win") and HAS_MSVCRT:
                    # Windows doesn't have native shared lock in msvcrt, use exclusive
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    lock_acquired = True
                    break
                elif not sys.platform.startswith("win") and HAS_FCNTL:
                    # Unix shared lock using fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                    lock_acquired = True
                    break
            except (OSError, IOError, BaseLockException):
                # Lock not available, wait and retry
                time.sleep(0.1)
                continue

        if not lock_acquired:
            f.close()
            raise TimeoutError(
                f"Could not acquire shared lock on {file_path} after {timeout} seconds"
            )

        # Lock acquired, yield file object
        yield f

    finally:
        # Release lock and close file
        try:
            if lock_acquired:
                if PORTALOCKER_AVAILABLE:
                    portalocker.unlock(f)
                elif sys.platform.startswith("win") and HAS_MSVCRT:
                    # Reset file pointer to beginning and unlock
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                elif not sys.platform.startswith("win") and HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"Error releasing shared lock on {file_path}: {e}")
        finally:
            f.close()
