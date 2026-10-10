"""
Sliding window implementation for metrics summarization in the Scientific Home Cluster.
"""

from collections import deque
from typing import Generic, TypeVar, Optional, Deque


T = TypeVar("T", int, float)


class SlidingWindow(Generic[T]):
    """
    A fixed-size sliding window for calculating statistics on recent values.
    """

    def __init__(self, size: int):
        """
        Initialize a sliding window.

        Args:
            size: Maximum number of elements to store in the window
        """
        if size <= 0:
            raise ValueError("Window size must be positive")

        self.size = size
        self._data: Deque[T] = deque(maxlen=size)

    def add(self, value: T, timestamp: Optional[float] = None) -> None:
        """
        Add a value to the sliding window.

        Args:
            value: Value to add
            timestamp: Optional timestamp (ignored in base class)
        """
        self._data.append(value)

    def get_values(self) -> list[T]:
        """
        Get a copy of all values in the window (oldest first).

        Returns:
            List of values in the window
        """
        return list(self._data)

    def clear(self) -> None:
        """Clear all values from the window."""
        self._data.clear()

    def __len__(self) -> int:
        """Get the current number of elements in the window."""
        return len(self._data)

    def is_empty(self) -> bool:
        """Check if the window is empty."""
        return len(self._data) == 0

    def is_full(self) -> bool:
        """Check if the window is full."""
        return len(self._data) == self.size

    def min(self) -> Optional[T]:
        """
        Get the minimum value in the window.

        Returns:
            Minimum value or None if window is empty
        """
        if not self._data:
            return None
        return min(self._data)

    def max(self) -> Optional[T]:
        """
        Get the maximum value in the window.

        Returns:
            Maximum value or None if window is empty
        """
        if not self._data:
            return None
        return max(self._data)

    def avg(self) -> Optional[float]:
        """
        Get the average value in the window.

        Returns:
            Average value or None if window is empty
        """
        if not self._data:
            return None
        return float(sum(self._data)) / len(self._data)

    def sum(self) -> Optional[T]:
        """
        Get the sum of values in the window.

        Returns:
            Sum of values or None if window is empty
        """
        if not self._data:
            return None
        return sum(self._data)


class TimestampedSlidingWindow(SlidingWindow[float]):
    """
    A sliding window that stores timestamped values and can calculate
    time-based statistics.
    """

    def __init__(self, size: int):
        """
        Initialize a timestamped sliding window.

        Args:
            size: Maximum number of elements to store in the window
        """
        super().__init__(size)
        self._timestamps: Deque[float] = deque(maxlen=size)

    def add(self, value: float, timestamp: Optional[float] = None) -> None:
        """
        Add a timestamped value to the sliding window.

        Args:
            value: Value to add
            timestamp: Timestamp associated with the value (seconds since epoch)
        """
        self._data.append(value)
        self._timestamps.append(timestamp if timestamp is not None else 0.0)

    def get_timestamps(self) -> list[float]:
        """
        Get a copy of all timestamps in the window (oldest first).

        Returns:
            List of timestamps in the window
        """
        return list(self._timestamps)

    def clear(self) -> None:
        """Clear all values and timestamps from the window."""
        super().clear()
        self._timestamps.clear()

    def get_latest_timestamp(self) -> Optional[float]:
        """
        Get the most recent timestamp in the window.

        Returns:
            Most recent timestamp or None if window is empty
        """
        if not self._timestamps:
            return None
        return self._timestamps[-1]

    def get_oldest_timestamp(self) -> Optional[float]:
        """
        Get the oldest timestamp in the window.

        Returns:
            Oldest timestamp or None if window is empty
        """
        if not self._timestamps:
            return None
        return self._timestamps[0]
