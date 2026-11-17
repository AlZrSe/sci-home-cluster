"""Subprocess-based tests for samples/sample_calc.py."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample_calc.py"
PYTHON = sys.executable

torch_available = importlib.util.find_spec("torch") is not None
numpy_available = importlib.util.find_spec("numpy") is not None


def _run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(SAMPLE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_smoke_exits_zero_and_prints_result() -> None:
    result = _run("--preset", "smoke")
    assert result.returncode == 0, result.stderr
    assert re.search(r"^RESULT ", result.stdout, re.MULTILINE) is not None


def test_invalid_size_exits_one() -> None:
    result = _run("--size", "0")
    assert result.returncode == 1
    assert "error:" in result.stderr


@pytest.mark.skipif(torch_available, reason="torch is installed")
def test_backend_torch_unavailable_exits_two() -> None:
    result = _run("--backend", "torch", "--preset", "smoke")
    assert result.returncode == 2
    assert "not available" in result.stderr


@pytest.mark.skipif(numpy_available, reason="numpy is installed")
def test_backend_numpy_unavailable_exits_two() -> None:
    result = _run("--backend", "numpy", "--preset", "smoke")
    assert result.returncode == 2
    assert "not available" in result.stderr


def test_determinism_smoke() -> None:
    r1 = _run("--preset", "smoke", "--json")
    r2 = _run("--preset", "smoke", "--json")
    assert r1.returncode == 0
    assert r2.returncode == 0
    d1 = json.loads(r1.stdout)
    d2 = json.loads(r2.stdout)
    assert d1["checksum"] == d2["checksum"]


def test_calibration_json() -> None:
    result = _run(
        "--preset",
        "smoke",
        "--calibrate",
        "--calibrate-seconds",
        "1",
        "--json",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["suggested_iters_for_60s"] > 0


def test_output_file_written(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    result = _run("--preset", "smoke", "--output", str(out))
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "checksum" in data
    assert data["exit_code"] == 0


def test_backend_cuda_unavailable_exits_two() -> None:
    # On this CPU-only host CUDA must be unavailable.
    import importlib

    torch = importlib.import_module("torch") if torch_available else None
    if torch is not None and torch.cuda.is_available():
        pytest.skip("CUDA is available")
    result = _run("--backend", "torch", "--device", "cuda", "--preset", "smoke")
    assert result.returncode == 2


@pytest.mark.slow
def test_cpu_1min_purepython_runs() -> None:
    result = _run("--backend", "purepython", "--preset", "cpu-1min", timeout=120)
    assert result.returncode == 0
    assert re.search(r"^RESULT ", result.stdout, re.MULTILINE) is not None
