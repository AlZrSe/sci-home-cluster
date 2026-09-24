"""Type stubs for portalocker."""

from typing import IO, Literal, overload

__version__: str

LOCK_EX: int
LOCK_SH: int
LOCK_NB: int
LOCK_UN: int

@overload
def lock(
    file: IO[bytes],
    flags: int,
    blocking: Literal[True] = True,
    timeout: float | None = None,
) -> None: ...
@overload
def lock(
    file: IO[bytes], flags: int, blocking: Literal[False], timeout: float | None = None
) -> bool: ...
def unlock(file: IO[bytes]) -> None: ...

class LockedFile:
    def __init__(
        self,
        filename: str,
        mode: str = "r",
        *,
        timeout: float | None = None,
        flags: int = LOCK_EX,
    ) -> None: ...
    def __enter__(self) -> IO[bytes]: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...
    @property
    def file(self) -> IO[bytes]: ...
