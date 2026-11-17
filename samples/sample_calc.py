#!/usr/bin/env python3
"""Sample calculation program for exercising the worker agent.

The program performs a deterministic repeated dense matrix multiplication and
prints a single ``RESULT`` line (or JSON). It is intentionally self-contained:
it uses only the Python standard library, plus ``numpy``/``torch`` when they
happen to be installed. No cluster/agent module is imported.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BACKEND = 2
EXIT_COMPUTE = 3
EXIT_INTERRUPTED = 130


# (preset, backend, device) -> (size, iters)
PRESETS: Dict[Tuple[str, str, str], Tuple[int, int]] = {
    ("smoke", "torch", "cpu"): (64, 2),
    ("smoke", "torch", "cuda"): (64, 2),
    ("smoke", "numpy", "cpu"): (64, 2),
    ("smoke", "purepython", "cpu"): (32, 2),
    ("cpu-1min", "torch", "cpu"): (2048, 1200),
    ("cpu-1min", "numpy", "cpu"): (2048, 500),
    ("cpu-1min", "purepython", "cpu"): (128, 460),
    ("gpu-1min", "torch", "cuda"): (4096, 2000),
}

BACKEND_DEFAULTS: Dict[str, Tuple[int, int]] = {
    "torch": (256, 10),
    "numpy": (256, 10),
    "purepython": (64, 10),
}


def _try_import(name: str) -> Optional[Any]:
    try:
        module = __import__(name)
        return module
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sample_calc.py",
        description="Deterministic repeated matrix multiplication workload.",
        exit_on_error=False,
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--backend", choices=("auto", "torch", "numpy", "purepython"), default="auto"
    )
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--iters", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--preset", choices=("smoke", "cpu-1min", "gpu-1min"), default="smoke"
    )
    parser.add_argument(
        "--progress-every", type=int, default=None, dest="progress_every"
    )
    parser.add_argument("--output", default=None)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument(
        "--calibrate-seconds", type=float, default=5.0, dest="calibrate_seconds"
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _progress(iters_done: int, iters_total: int, start: float) -> None:
    elapsed = time.perf_counter() - start
    if iters_done > 0:
        eta = (elapsed / iters_done) * (iters_total - iters_done)
    else:
        eta = 0.0
    print(
        f"PROGRESS iters={iters_done}/{iters_total} "
        f"elapsed={elapsed:.2f}s eta={eta:.2f}s",
        file=sys.stderr,
        flush=True,
    )


def _run_purepython(
    size: int, iters: int, seed: int, progress_every: int
) -> Tuple[float, float]:
    rng = random.Random(seed)
    a_mat = [[rng.random() for _ in range(size)] for _ in range(size)]
    b_mat = [[rng.random() for _ in range(size)] for _ in range(size)]
    checksum = 0.0
    start = time.perf_counter()
    for i in range(1, iters + 1):
        c_mat = [[0.0] * size for _ in range(size)]
        for r in range(size):
            a_row = a_mat[r]
            c_row = c_mat[r]
            for k in range(size):
                a_val = a_row[k]
                b_row = b_mat[k]
                for c in range(size):
                    c_row[c] += a_val * b_row[c]
        total = 0.0
        for row in c_mat:
            total += sum(row)
        checksum += total
        if i % progress_every == 0 or i == iters:
            _progress(i, iters, start)
    elapsed = time.perf_counter() - start
    return checksum, elapsed


def _run_numpy(
    np_mod: Any, size: int, iters: int, seed: int, progress_every: int
) -> Tuple[float, float]:
    rng = np_mod.random.default_rng(seed)
    a_mat = rng.random((size, size))
    b_mat = rng.random((size, size))
    checksum = 0.0
    start = time.perf_counter()
    for i in range(1, iters + 1):
        c_mat = a_mat @ b_mat
        checksum += float(c_mat.sum())
        if i % progress_every == 0 or i == iters:
            _progress(i, iters, start)
    elapsed = time.perf_counter() - start
    return checksum, elapsed


def _run_torch(
    torch_mod: Any,
    device: str,
    size: int,
    iters: int,
    seed: int,
    progress_every: int,
) -> Tuple[float, float]:
    torch_mod.manual_seed(seed)
    try:
        torch_mod.use_deterministic_algorithms(True)
    except Exception:
        pass
    dev = torch_mod.device(device)
    a_mat = torch_mod.rand(size, size, device=dev)
    b_mat = torch_mod.rand(size, size, device=dev)
    if device == "cuda":
        torch_mod.cuda.synchronize()
    checksum = 0.0
    start = time.perf_counter()
    with torch_mod.no_grad():
        for i in range(1, iters + 1):
            c_mat = a_mat @ b_mat
            checksum += float(c_mat.sum().item())
            if i % progress_every == 0 or i == iters:
                _progress(i, iters, start)
    if device == "cuda":
        torch_mod.cuda.synchronize()
    elapsed = time.perf_counter() - start
    return checksum, elapsed


def _make_one_iter(
    backend: str,
    device: str,
    size: int,
    seed: int,
    torch_mod: Optional[Any],
    numpy_mod: Optional[Any],
) -> Callable[[], None]:
    if backend == "purepython":
        rng = random.Random(seed)
        a_mat = [[rng.random() for _ in range(size)] for _ in range(size)]
        b_mat = [[rng.random() for _ in range(size)] for _ in range(size)]

        def _one() -> None:
            c_mat = [[0.0] * size for _ in range(size)]
            for r in range(size):
                a_row = a_mat[r]
                c_row = c_mat[r]
                for k in range(size):
                    a_val = a_row[k]
                    b_row = b_mat[k]
                    for c in range(size):
                        c_row[c] += a_val * b_row[c]

        return _one

    if backend == "numpy":
        assert numpy_mod is not None
        rng = numpy_mod.random.default_rng(seed)
        a_mat = rng.random((size, size))  # type: ignore
        b_mat = rng.random((size, size))  # type: ignore

        def _one() -> None:  # type: ignore[no-redef]
            _ = a_mat @ b_mat  # type: ignore

        return _one

    # torch
    assert torch_mod is not None
    torch_mod.manual_seed(seed)
    try:
        torch_mod.use_deterministic_algorithms(True)
    except Exception:
        pass
    dev = torch_mod.device(device)
    a_mat = torch_mod.rand(size, size, device=dev)
    b_mat = torch_mod.rand(size, size, device=dev)

    def _one() -> None:  # type: ignore[no-redef]
        with torch_mod.no_grad():  # type: ignore[union-attr]
            _ = a_mat @ b_mat  # type: ignore

    return _one


def run_calibration(
    backend: str,
    device: str,
    size: int,
    seed: int,
    seconds: float,
    torch_mod: Optional[Any],
    numpy_mod: Optional[Any],
    as_json: bool,
) -> int:
    one_iter = _make_one_iter(backend, device, size, seed, torch_mod, numpy_mod)

    # Warm-up (discarded).
    warm_start = time.perf_counter()
    while time.perf_counter() - warm_start < 0.2:
        one_iter()

    iters = 0
    start = time.perf_counter()
    while time.perf_counter() - start < seconds:
        one_iter()
        iters += 1
    elapsed = time.perf_counter() - start
    ips = (iters / elapsed) if elapsed > 0 else 0.0
    suggested = int(round(ips * 60.0))

    result = {
        "backend": backend,
        "device": device,
        "size": size,
        "calibrate_seconds": seconds,
        "iters_completed": iters,
        "iters_per_second": round(ips, 2),
        "suggested_iters_for_60s": suggested,
    }
    print(json.dumps(result))
    return EXIT_OK


def _resolve_backend(
    args: argparse.Namespace,
    torch_mod: Optional[Any],
    numpy_mod: Optional[Any],
    cuda_available: bool,
) -> Tuple[str, int, Optional[str]]:
    """Return (backend, exit_code, error_message)."""
    device_req = args.device
    if device_req == "cuda" and not cuda_available:
        return "", EXIT_BACKEND, "error: CUDA is not available on this machine"

    backend_req = args.backend
    if backend_req == "auto":
        if device_req == "cuda":
            backend = "torch"
        elif torch_mod is not None:
            backend = "torch"
        elif numpy_mod is not None:
            backend = "numpy"
        else:
            backend = "purepython"
    else:
        backend = backend_req

    if backend == "torch" and torch_mod is None:
        return (
            backend,
            EXIT_BACKEND,
            ("error: backend 'torch' is not available (module not installed)"),
        )
    if backend == "numpy" and numpy_mod is None:
        return (
            backend,
            EXIT_BACKEND,
            ("error: backend 'numpy' is not available (module not installed)"),
        )
    if backend not in ("torch", "numpy", "purepython"):
        return backend, EXIT_USAGE, f"error: unknown backend '{backend}'"
    return backend, EXIT_OK, None


def _resolve_device(
    args: argparse.Namespace,
    backend: str,
    cuda_available: bool,
) -> Tuple[str, int, Optional[str]]:
    """Return (device, exit_code, error_message)."""
    device_req = args.device
    if device_req == "auto":
        if backend == "torch" and cuda_available and args.preset == "gpu-1min":
            device = "cuda"
        else:
            device = "cpu"
    else:
        device = device_req

    if device == "cuda" and backend != "torch":
        return (
            device,
            EXIT_BACKEND,
            (f"error: backend '{backend}' does not support CUDA"),
        )
    if device == "cuda" and not cuda_available:
        return device, EXIT_BACKEND, ("error: CUDA is not available on this machine")
    if args.preset == "gpu-1min" and not (backend == "torch" and device == "cuda"):
        return device, EXIT_BACKEND, ("error: gpu-1min preset requires torch with CUDA")
    return device, EXIT_OK, None


def _resolve_backend_and_device(
    args: argparse.Namespace,
    torch_mod: Optional[Any],
    numpy_mod: Optional[Any],
    cuda_available: bool,
) -> Tuple[str, str, int, Optional[str]]:
    """Return (backend, device, exit_code, error_message)."""
    backend, code, message = _resolve_backend(
        args, torch_mod, numpy_mod, cuda_available
    )
    if message is not None:
        return backend, "", code, message
    device, code, message = _resolve_device(args, backend, cuda_available)
    if message is not None:
        return backend, device, code, message
    return backend, device, EXIT_OK, None


def _resolve_size(
    args: argparse.Namespace, backend: str, device: str
) -> Tuple[int, int]:
    size = args.size
    iters = args.iters
    if size is None or iters is None:
        key = (args.preset, backend, device)
        if key not in PRESETS and backend == "torch" and device == "cuda":
            alt = (args.preset, "torch", "cpu")
            if alt in PRESETS:
                key = alt
        if key in PRESETS:
            p_size, p_iters = PRESETS[key]
            if size is None:
                size = p_size
            if iters is None:
                iters = p_iters
        else:
            if size is None:
                size = BACKEND_DEFAULTS[backend][0]
            if iters is None:
                iters = BACKEND_DEFAULTS[backend][1]
    assert size is not None and iters is not None
    return size, iters


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if args.size is not None and args.size <= 0:
        return "error: size must be positive"
    if args.iters is not None and args.iters <= 0:
        return "error: iters must be positive"
    if args.progress_every is not None and args.progress_every <= 0:
        return "error: progress-every must be positive"
    if args.calibrate_seconds <= 0:
        return "error: calibrate-seconds must be positive"
    return None


def _execute_workload(
    backend: str,
    torch_mod: Optional[Any],
    numpy_mod: Optional[Any],
    device: str,
    size: int,
    iters: int,
    seed: int,
    progress_every: int,
) -> Tuple[Optional[float], Optional[float], int, Optional[str]]:
    try:
        if backend == "purepython":
            checksum, elapsed = _run_purepython(size, iters, seed, progress_every)
        elif backend == "numpy":
            checksum, elapsed = _run_numpy(numpy_mod, size, iters, seed, progress_every)
        else:
            checksum, elapsed = _run_torch(
                torch_mod, device, size, iters, seed, progress_every
            )
    except KeyboardInterrupt:
        return None, None, EXIT_INTERRUPTED, "interrupted"
    except Exception as exc:
        return None, None, EXIT_COMPUTE, f"error: computation failed: {exc}"
    return checksum, elapsed, EXIT_OK, None


def _write_output(out_path: Path, result: Dict[str, Any]) -> Optional[str]:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_path, out_path)
    except Exception as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return f"error: failed to write output: {exc}"
    return None


def _emit_result(
    args: argparse.Namespace, result: Dict[str, Any]
) -> Tuple[int, Optional[str]]:
    if args.json:
        print(json.dumps(result))
    else:
        ips = result["iters_per_second"]
        print(
            f"RESULT backend={result['backend']} device={result['device']} "
            f"size={result['size']} iters={result['iters']} "
            f"seed={result['seed']} checksum={result['checksum']} "
            f"elapsed_seconds={result['elapsed_seconds']:.2f} "
            f"iters_per_second={ips:.2f}"
        )
    if args.output:
        out_path = Path(args.output)
        error = _write_output(out_path, result)
        if error is not None:
            return EXIT_USAGE, error
    return EXIT_OK, None


def _parse_args(
    argv: Optional[list],
) -> Tuple[Optional[argparse.Namespace], int, Optional[str]]:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return None, (int(code) if isinstance(code, int) else 0), None
    except argparse.ArgumentError as exc:
        return None, EXIT_USAGE, f"error: {exc}"
    return args, EXIT_OK, None


def main(argv: Optional[list] = None) -> int:
    args, code, message = _parse_args(argv)
    if args is None:
        if message is not None:
            print(message, file=sys.stderr)
        return code

    message = _validate_args(args)
    if message is not None:
        print(message, file=sys.stderr)
        return EXIT_USAGE

    torch_mod = _try_import("torch")
    numpy_mod = _try_import("numpy")

    cuda_available = bool(
        torch_mod is not None
        and getattr(torch_mod, "cuda", None) is not None
        and torch_mod.cuda.is_available()
    )

    backend, device, code, message = _resolve_backend_and_device(
        args, torch_mod, numpy_mod, cuda_available
    )
    if message is not None:
        print(message, file=sys.stderr)
        return code

    size, iters = _resolve_size(args, backend, device)

    if size <= 0 or iters <= 0:
        print("error: size/iters must be positive", file=sys.stderr)
        return EXIT_USAGE

    progress_every = args.progress_every
    if progress_every is None:
        progress_every = max(1, iters // 10)

    if args.calibrate:
        return run_calibration(
            backend,
            device,
            size,
            args.seed,
            args.calibrate_seconds,
            torch_mod,
            numpy_mod,
            args.json,
        )

    checksum, elapsed, code, message = _execute_workload(
        backend,
        torch_mod,
        numpy_mod,
        device,
        size,
        iters,
        args.seed,
        progress_every,
    )
    if message is not None:
        print(message, file=sys.stderr)
        return code

    assert checksum is not None
    assert elapsed is not None
    ips = (iters / elapsed) if elapsed > 0 else 0.0
    result = {
        "backend": backend,
        "device": device,
        "size": size,
        "iters": iters,
        "seed": args.seed,
        "checksum": checksum,
        "elapsed_seconds": round(elapsed, 2),
        "iters_per_second": round(ips, 2),
        "exit_code": EXIT_OK,
    }

    code, message = _emit_result(args, result)
    if message is not None:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(EXIT_INTERRUPTED)
