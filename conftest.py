"""Rootdir conftest: arms the shared signal safety guard at session start."""

from shared.testing.signal_guard import (
    pytest_configure,
    pytest_unconfigure,
    pytest_runtest_logreport,
    pytest_runtest_protocol,
    pytest_sessionfinish,
    SIGNAL_GUARD,
)

# Arm the guard immediately at import time, before pytest_configure is called.
# This covers the window between process start and pytest_configure.
SIGNAL_GUARD.install()

# Re-export the hooks so pytest discovers them at rootdir level
__all__ = [
    "pytest_configure",
    "pytest_unconfigure",
    "pytest_runtest_logreport",
    "pytest_runtest_protocol",
    "pytest_sessionfinish",
]
