"""
Windows-compatible atomic write utilities.

Provides atomic file writes with retry logic for Windows PermissionError (WinError 5)
when the destination file is open by another process without FILE_SHARE_DELETE.
"""

import os
import time
import logging
from pathlib import Path
from typing import Final, Optional

logger = logging.getLogger(__name__)

# Default retry configuration (overridable via environment)
_DEFAULT_MAX_ATTEMPTS: Final[int] = int(os.getenv("SHC_ATOMIC_WRITE_MAX_ATTEMPTS", "5"))
_DEFAULT_BASE_DELAY_MS: Final[int] = int(
    os.getenv("SHC_ATOMIC_WRITE_BASE_DELAY_MS", "50")
)
_DEFAULT_MAX_DELAY_MS: Final[int] = int(
    os.getenv("SHC_ATOMIC_WRITE_MAX_DELAY_MS", "500")
)

_MAX_ATTEMPTS: Final[int] = _DEFAULT_MAX_ATTEMPTS
_BASE_RETRY_DELAY_S: Final[float] = _DEFAULT_BASE_DELAY_MS / 1000.0
_MAX_RETRY_DELAY_S: Final[float] = _DEFAULT_MAX_DELAY_MS / 1000.0


def _is_retryable_error(error: OSError) -> bool:
    """
    Determine if an OSError is retryable.

    On Windows: PermissionError (WinError 5) when destination is open without
    FILE_SHARE_DELETE. On POSIX: PermissionError (errno 13) - rare for replace
    but handled uniformly.
    """
    if not isinstance(error, PermissionError):
        return False
    # Check for Windows-specific error code
    if hasattr(error, "winerror") and error.winerror == 5:
        return True
    # POSIX EACCES (13) - possible but unlikely for os.replace
    if error.errno == 13:
        return True
    return False


def atomic_replace_with_retry(src: Path, dst: Path) -> None:
    """
    Atomically replace dst with src, retrying on Windows PermissionError.

    On Windows, os.replace fails with WinError 5 if dst is open without
    FILE_SHARE_DELETE. We retry with exponential backoff because readers
    (watchdog, editors, tail) typically close the handle within milliseconds.

    Args:
        src: Source file path (temporary file)
        dst: Destination file path

    Raises:
        OSError: If all retries exhausted, the last PermissionError is raised.
        Other OSErrors are raised immediately (no retry).
    """
    attempt = 0
    last_error: Optional[OSError] = None

    while attempt < _MAX_ATTEMPTS:
        try:
            os.replace(src, dst)
            if attempt > 0:
                logger.info(
                    "Atomic replace succeeded on attempt %d/%d: %s -> %s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    src,
                    dst,
                )
            return
        except PermissionError as e:
            # Windows: ERROR_ACCESS_DENIED (5) when destination is open
            # POSIX: EACCES (13) - unlikely for replace but handle uniformly
            last_error = e
            attempt += 1
            if attempt < _MAX_ATTEMPTS:
                delay = min(
                    _BASE_RETRY_DELAY_S * (2 ** (attempt - 1)), _MAX_RETRY_DELAY_S
                )
                logger.warning(
                    "Atomic replace failed (attempt %d/%d): %s -> %s: %s. "
                    "Retrying in %.3fs...",
                    attempt,
                    _MAX_ATTEMPTS,
                    src,
                    dst,
                    e,
                    delay,
                )
                time.sleep(delay)
            continue
        except OSError as e:
            # Any other OSError (ENOSPC, EROFS, etc.) - fail immediately
            logger.error(
                "Atomic replace failed with non-retryable error: %s -> %s: %s",
                src,
                dst,
                e,
            )
            raise

    # All retries exhausted
    logger.error(
        "Atomic replace failed after %d attempts: %s -> %s: %s",
        _MAX_ATTEMPTS,
        src,
        dst,
        last_error,
    )
    assert last_error is not None
    raise last_error


def atomic_write(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    encoding: str = "utf-8",
) -> None:
    """
    Write content to path atomically with Windows-compatible retry.

    Creates a temporary file in the same directory, writes content,
    sets permissions, then atomically replaces the target file.
    Retries on Windows PermissionError when destination is open.

    Args:
        path: Destination file path
        content: String content to write
        mode: File permissions (default: 0o600)
        encoding: Text encoding (default: utf-8)

    Raises:
        OSError: If write fails after all retries
    """
    path = Path(path)
    directory = path.parent
    if directory and not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)

    # Create temporary file in same directory
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=directory,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
        encoding=encoding,
    ) as tmp_file:
        tmp_file.write(content)
        tmp_file_path = Path(tmp_file.name)

    try:
        # Set permissions on temp file BEFORE replace so final file is never
        # briefly world-readable (critical for secrets)
        try:
            tmp_file_path.chmod(mode)
        except OSError:
            # chmod may fail on some filesystems (FAT, network mounts); log
            # at debug and continue - the key in memory is still usable.
            logger.debug(
                "Could not set %o permissions on %s; continuing anyway.",
                mode,
                tmp_file_path,
            )

        # Atomically replace with retry
        atomic_replace_with_retry(tmp_file_path, path)

    except Exception:
        # Clean up temporary file on error
        try:
            tmp_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
