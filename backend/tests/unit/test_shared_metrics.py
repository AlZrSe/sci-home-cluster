"""
Unit tests for shared metrics summarization.
"""

from shared.metrics.summarizer import GPUMetricsSummarizer, CPUMetricsSummarizer
from shared.metrics.sliding_window import SlidingWindow, TimestampedSlidingWindow


def test_sliding_window_basic():
    """Test basic SlidingWindow functionality."""
    window = SlidingWindow[int](size=3)

    # Test adding values
    window.add(1)
    window.add(2)
    window.add(3)

    assert len(window) == 3
    assert window.get_values() == [1, 2, 3]
    assert window.min() == 1
    assert window.max() == 3
    assert window.avg() == 2.0
    assert window.sum() == 6

    # Test window overflow (oldest value dropped)
    window.add(4)
    assert len(window) == 3  # Still at max size
    assert window.get_values() == [2, 3, 4]  # 1 was dropped
    assert window.min() == 2
    assert window.max() == 4
    assert window.avg() == 3.0

    # Test clear
    window.clear()
    assert len(window) == 0
    assert window.is_empty()
    assert window.get_values() == []
    assert window.min() is None
    assert window.max() is None
    assert window.avg() is None
    assert window.sum() is None


def test_timestamped_sliding_window():
    """Test TimestampedSlidingWindow functionality."""
    window = TimestampedSlidingWindow(size=3)
    base_time = 1000.0

    # Add timestamped values
    window.add(10.0, base_time)
    window.add(20.0, base_time + 1)
    window.add(30.0, base_time + 2)

    assert len(window) == 3
    assert window.get_values() == [10.0, 20.0, 30.0]
    assert window.get_timestamps() == [base_time, base_time + 1, base_time + 2]
    assert window.get_latest_timestamp() == base_time + 2
    assert window.get_oldest_timestamp() == base_time

    # Test adding another value (should drop oldest)
    window.add(40.0, base_time + 3)
    assert len(window) == 3
    assert window.get_values() == [20.0, 30.0, 40.0]
    assert window.get_timestamps() == [base_time + 1, base_time + 2, base_time + 3]
    assert window.get_latest_timestamp() == base_time + 3
    assert window.get_oldest_timestamp() == base_time + 1

    # Test clear
    window.clear()
    assert len(window) == 0
    assert window.is_empty()
    assert window.get_latest_timestamp() is None
    assert window.get_oldest_timestamp() is None


def test_gpu_metrics_summarizer():
    """Test GPUMetricsSummarizer functionality."""
    summarizer = GPUMetricsSummarizer(window_size=5)  # 5 samples

    # Add some samples
    summarizer.add_sample(memory_used_mb=1000, utilization_percent=50, temperature_c=60)
    summarizer.add_sample(memory_used_mb=1200, utilization_percent=60, temperature_c=62)
    summarizer.add_sample(memory_used_mb=1100, utilization_percent=55, temperature_c=61)

    summary = summarizer.get_summary()

    # Check averages
    assert summary["gpu_memory_avg_mb"] == 1100  # (1000+1200+1100)/3
    assert summary["gpu_util_avg"] == 55  # (50+60+55)/3
    assert summary["temperature_avg_c"] == 61  # (60+62+61)/3

    # Check mins
    assert summary["gpu_memory_min_mb"] == 1000
    assert summary["gpu_util_min"] == 50
    assert summary["temperature_min_c"] == 60

    # Check maxs
    assert summary["gpu_memory_max_mb"] == 1200
    assert summary["gpu_util_max"] == 60
    assert summary["temperature_max_c"] == 62

    # Test clear
    summarizer.clear()
    summary = summarizer.get_summary()
    assert summary["gpu_memory_avg_mb"] == 0
    assert summary["gpu_util_avg"] == 0
    assert summary["temperature_avg_c"] == 0


def test_cpu_metrics_summarizer():
    """Test CPUMetricsSummarizer functionality."""
    summarizer = CPUMetricsSummarizer(window_size=5)

    # Add some samples
    summarizer.add_sample(cpu_percent=40.0, memory_percent=50.0)
    summarizer.add_sample(cpu_percent=60.0, memory_percent=70.0)
    summarizer.add_sample(cpu_percent=50.0, memory_percent=60.0)

    summary = summarizer.get_summary()

    # Check averages
    assert summary["cpu_avg_percent"] == 50.0  # (40+60+50)/3
    assert summary["memory_avg_percent"] == 60.0  # (50+70+60)/3

    # Test clear
    summarizer.clear()
    summary = summarizer.get_summary()
    assert summary["cpu_avg_percent"] == 0.0
    assert summary["memory_avg_percent"] == 0.0


def test_sliding_window_edge_cases():
    """Test edge cases for sliding windows."""
    # Empty window
    window = SlidingWindow[float](size=5)
    assert window.min() is None
    assert window.max() is None
    assert window.avg() is None
    assert window.sum() is None
    assert len(window) == 0
    assert window.is_empty()
    assert not window.is_full()

    # Single value
    window.add(42.0)
    assert window.min() == 42.0
    assert window.max() == 42.0
    assert window.avg() == 42.0
    assert window.sum() == 42.0
    assert len(window) == 1
    assert not window.is_empty()
    assert not window.is_full()

    # Window exactly full
    window.add(1.0)
    window.add(2.0)
    window.add(3.0)
    window.add(4.0)
    assert len(window) == 5
    assert window.is_full()
    assert not window.is_empty()

    # Test that is_full works correctly
    window.add(5.0)  # This should make it full and drop the oldest
    assert len(window) == 5  # Still full
    assert window.is_full()
    assert not window.is_empty()


def test_gpu_metrics_summarizer_window_overflow():
    """Test that GPU summarizer correctly handles window overflow."""
    summarizer = GPUMetricsSummarizer(window_size=3)

    # Add more samples than window size
    summarizer.add_sample(memory_used_mb=1000, utilization_percent=50, temperature_c=60)
    summarizer.add_sample(memory_used_mb=1200, utilization_percent=60, temperature_c=62)
    summarizer.add_sample(memory_used_mb=1100, utilization_percent=55, temperature_c=61)
    summarizer.add_sample(
        memory_used_mb=1300, utilization_percent=70, temperature_c=65
    )  # This should drop first sample

    summary = summarizer.get_summary()

    # Should only contain last 3 samples: (1200,1100,1300), (60,55,70), (62,61,65)
    assert summary["gpu_memory_avg_mb"] == 1200  # (1200+1100+1300)/3
    assert summary["gpu_util_avg"] == 61  # (60+55+70)/3
    assert summary["temperature_avg_c"] == 62  # (62+61+65)/3

    # Min/max should reflect the last 3 samples
    assert summary["gpu_memory_min_mb"] == 1100
    assert summary["gpu_memory_max_mb"] == 1300
    assert summary["gpu_util_min"] == 55
    assert summary["gpu_util_max"] == 70
    assert summary["temperature_min_c"] == 61
    assert summary["temperature_max_c"] == 65


def test_cpu_metrics_summarizer_window_overflow():
    """Test that CPU summarizer correctly handles window overflow."""
    summarizer = CPUMetricsSummarizer(window_size=2)

    # Add samples
    summarizer.add_sample(cpu_percent=40.0, memory_percent=50.0)
    summarizer.add_sample(cpu_percent=60.0, memory_percent=70.0)
    summarizer.add_sample(
        cpu_percent=80.0, memory_percent=90.0
    )  # This should drop first sample

    summary = summarizer.get_summary()

    # Should only contain last 2 samples: (60,80) and (70,90)
    assert summary["cpu_avg_percent"] == 70.0  # (60+80)/2
    assert summary["memory_avg_percent"] == 80.0  # (70+90)/2

    # Min/max should reflect the last 2 samples
    assert summary["cpu_avg_percent"] == 70.0
    # Note: The current implementation only returns avg, not min/max for CPU summarizer
    # This matches the implementation in summarizer.py


def test_metrics_summarizer_different_window_sizes():
    """Test summarizers with different window sizes."""
    # Small window
    small_gpu = GPUMetricsSummarizer(window_size=2)
    small_cpu = CPUMetricsSummarizer(window_size=2)

    # Large window
    large_gpu = GPUMetricsSummarizer(window_size=10)
    large_cpu = CPUMetricsSummarizer(window_size=10)

    # Add same samples to both
    samples = [
        (1000, 50, 60, 40.0, 50.0),
        (1200, 60, 62, 60.0, 70.0),
        (1100, 55, 61, 50.0, 60.0),
    ]

    for mem, util, temp, cpu, mem_pct in samples:
        small_gpu.add_sample(mem, util, temp)
        small_cpu.add_sample(cpu, mem_pct)
        large_gpu.add_sample(mem, util, temp)
        large_cpu.add_sample(cpu, mem_pct)

    # Small window should only have last 2 samples
    small_gpu_summary = small_gpu.get_summary()
    small_cpu_summary = small_cpu.get_summary()

    # Large window should have all 3 samples
    large_gpu_summary = large_gpu.get_summary()
    large_cpu_summary = large_cpu.get_summary()

    # GPU memory: small window avg of (1200,1100) = 1150
    # GPU memory: large window avg of (1000,1200,1100) = 1100
    assert small_gpu_summary["gpu_memory_avg_mb"] == 1150
    assert large_gpu_summary["gpu_memory_avg_mb"] == 1100

    # CPU utilization: small window avg of (60,50) = 55
    # CPU utilization: large window avg of (40,60,50) = 50
    assert small_cpu_summary["cpu_avg_percent"] == 55.0
    assert large_cpu_summary["cpu_avg_percent"] == 50.0
