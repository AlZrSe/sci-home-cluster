"""
Metrics summarization for the Scientific Home Cluster.
Provides rolling window statistics for GPU and CPU metrics.
"""

from typing import Optional
from .sliding_window import TimestampedSlidingWindow


class GPUMetricsSummarizer:
    """
    Rolling window statistics for GPU metrics.
    Maintains 30-second sliding window of GPU measurements.
    """

    def __init__(self, window_size: int = 60):
        """
        Initialize GPU metrics summarizer.

        Args:
            window_size: Number of samples to keep (default 60 = 30s at 0.5s intervals)
        """
        self.window_size = window_size
        self.memory_used = TimestampedSlidingWindow(window_size)
        self.utilization = TimestampedSlidingWindow(window_size)
        self.temperature = TimestampedSlidingWindow(window_size)

    def add_sample(
        self,
        memory_used_mb: int,
        utilization_percent: int,
        temperature_c: int,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Add a GPU metrics sample.

        Args:
            memory_used_mb: GPU memory used in MB
            utilization_percent: GPU utilization percentage
            temperature_c: GPU temperature in Celsius
            timestamp: Unix timestamp (seconds since epoch). If None, uses current time.
        """
        import time

        if timestamp is None:
            timestamp = time.time()

        self.memory_used.add(float(memory_used_mb), timestamp)
        self.utilization.add(float(utilization_percent), timestamp)
        self.temperature.add(float(temperature_c), timestamp)

    def get_summary(self) -> dict:
        """
        Get current GPU metrics summary.

        Returns:
            Dictionary with min/max/avg for memory, utilization, and temperature
        """
        return {
            "gpu_memory_min_mb": int(self.memory_used.min())
            if self.memory_used.min() is not None
            else 0,
            "gpu_memory_max_mb": int(self.memory_used.max())
            if self.memory_used.max() is not None
            else 0,
            "gpu_memory_avg_mb": int(self.memory_used.avg())
            if self.memory_used.avg() is not None
            else 0,
            "gpu_util_min": int(self.utilization.min())
            if self.utilization.min() is not None
            else 0,
            "gpu_util_max": int(self.utilization.max())
            if self.utilization.max() is not None
            else 0,
            "gpu_util_avg": int(self.utilization.avg())
            if self.utilization.avg() is not None
            else 0,
            "temperature_min_c": int(self.temperature.min())
            if self.temperature.min() is not None
            else 0,
            "temperature_max_c": int(self.temperature.max())
            if self.temperature.max() is not None
            else 0,
            "temperature_avg_c": int(self.temperature.avg())
            if self.temperature.avg() is not None
            else 0,
        }

    def clear(self) -> None:
        """Clear all samples."""
        self.memory_used.clear()
        self.utilization.clear()
        self.temperature.clear()


class CPUMetricsSummarizer:
    """
    Rolling window statistics for CPU metrics.
    Maintains 30-second sliding window of CPU measurements.
    """

    def __init__(self, window_size: int = 60):
        """
        Initialize CPU metrics summarizer.

        Args:
            window_size: Number of samples to keep (default 60 = 30s at 0.5s intervals)
        """
        self.window_size = window_size
        self.cpu_percent = TimestampedSlidingWindow(window_size)
        self.memory_percent = TimestampedSlidingWindow(window_size)

    def add_sample(
        self,
        cpu_percent: float,
        memory_percent: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Add a CPU metrics sample.

        Args:
            cpu_percent: CPU usage percentage
            memory_percent: Memory usage percentage
            timestamp: Unix timestamp (seconds since epoch). If None, uses current time.
        """
        import time

        if timestamp is None:
            timestamp = time.time()

        self.cpu_percent.add(cpu_percent, timestamp)
        self.memory_percent.add(memory_percent, timestamp)

    def get_summary(self) -> dict:
        """
        Get current CPU metrics summary.

        Returns:
            Dictionary with min/max/avg for CPU and memory usage
        """
        return {
            "cpu_avg_percent": self.cpu_percent.avg()
            if self.cpu_percent.avg() is not None
            else 0.0,
            "memory_avg_percent": self.memory_percent.avg()
            if self.memory_percent.avg() is not None
            else 0.0,
        }

    def clear(self) -> None:
        """Clear all samples."""
        self.cpu_percent.clear()
        self.memory_percent.clear()
