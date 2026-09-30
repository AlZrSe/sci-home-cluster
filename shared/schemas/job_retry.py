"""
Deprecated alias module. Use :mod:`shared.schemas.retry` instead.
"""

from .retry import JobRetry, RetryPolicy

__all__ = ["JobRetry", "RetryPolicy"]
