"""
Statistical timing test to verify constant-time comparison.
This test is marked as @pytest.mark.slow and may be skipped in CI.
Run manually to verify timing resistance.
"""

import statistics
import time
from unittest.mock import patch

import pytest

from backend.services.auth_service import AuthService
from backend.core.config import settings


@pytest.mark.slow
@pytest.mark.asyncio
async def test_verify_shared_token_constant_time_timing():
    """
    Measure response times for verify_shared_token valid vs invalid tokens.
    This function only does constant-time comparison, so timing should be consistent.
    """
    auth_service = AuthService()
    with patch.object(settings, "SHARED_TOKEN", "x" * 32):  # 32-char token
        valid_times = []
        invalid_times = []

        for _ in range(1000):
            # Valid token
            start = time.perf_counter_ns()
            await auth_service.verify_shared_token("x" * 32)
            valid_times.append(time.perf_counter_ns() - start)

            # Invalid token (differs at first char)
            start = time.perf_counter_ns()
            await auth_service.verify_shared_token("y" + "x" * 31)
            invalid_times.append(time.perf_counter_ns() - start)

        # Statistical test: means should be close (within 2 standard deviations)
        valid_mean = statistics.mean(valid_times)
        invalid_mean = statistics.mean(invalid_times)
        valid_stdev = statistics.stdev(valid_times)
        invalid_stdev = statistics.stdev(invalid_times)

        # Difference should be less than 2 standard deviations
        diff = abs(valid_mean - invalid_mean)
        pooled_stdev = (valid_stdev + invalid_stdev) / 2
        assert diff < 2 * pooled_stdev, (
            f"Timing difference detected: valid={valid_mean}ns, "
            f"invalid={invalid_mean}ns, diff={diff}ns, "
            f"pooled_stdev={pooled_stdev}ns"
        )
