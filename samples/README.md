# Sample Calculation Program

`sample_calc.py` is a self-contained, deterministic workload used to exercise
and test the worker agent's job execution path (submission, claim, execution,
log capture, terminal state). It runs on CPU and, when available, on GPU (CUDA).

## Usage

```bash
python samples/sample_calc.py --preset smoke
python samples/sample_calc.py --preset cpu-1min --seed 42 --progress-every 100
python samples/sample_calc.py --preset gpu-1min --device cuda
python samples/sample_calc.py --calibrate --json
python samples/sample_calc.py --preset smoke --output result.json
```

Wrappers are provided for convenience: `samples/sample_calc` (POSIX) and
`samples/sample_calc.cmd` (Windows).

## Presets

| Preset | Backend | Device | Size | Iters | Target runtime |
|---|---|---|---|---|---|
| smoke | auto | cpu | 64/32 | 2 | < 5 s |
| cpu-1min | torch | cpu | 2048 | 1200 | ~60 s |
| cpu-1min | numpy | cpu | 2048 | 500 | ~60 s |
| cpu-1min | purepython | cpu | 128 | 460 | ~60 s |
| gpu-1min | torch | cuda | 4096 | 2000 (initial) | ~60 s |

Final GPU numbers must be re-profiled on a GPU node with `--calibrate`.

## Profiling table (measured)

| Backend | Size | s/iter | Preset iters | Notes |
|---|---|---|---|---|
| torch (CPU) | 2048 | 0.0508 | 1200 | MKL/oneDNN |
| numpy | 2048 | 0.1209 | 500 | OpenBLAS |
| purepython | 128 | 0.1155 | 519 | triple loop (measured on this host) |
| purepython | 32 | 0.0019 | 31598 | triple loop (smoke) |
| torch (CUDA) | 4096 | to be profiled | 2000 (initial) | no CUDA on dev host; use `--calibrate` |

> **Note**: `torch` and `numpy` rows are from the original dev host (PM's measurements).
> The `purepython` rows are measured on this host (Windows 11, Python 3.12).
> Run `--calibrate --json` on each node to derive presets for that hardware.

## Profiling / Calibration

The `--calibrate` mode measures iterations/second for the selected backend/size
and prints a suggested `--iters` value for a 60 s run:

```bash
python sample_calc.py --preset cpu-1min --calibrate --calibrate-seconds 5 --json
```

Example output (purepython, size=128):
```json
{
  "backend": "purepython",
  "device": "cpu",
  "size": 128,
  "calibrate_seconds": 5.0,
  "iters_completed": 43,
  "iters_per_second": 8.6,
  "suggested_iters_for_60s": 516
}
```

Use the suggested value with explicit flags to get a ~60 s run on the current
machine:

```bash
python sample_calc.py --backend purepython --size 128 --iters 516 --seed 42
```

## Distribution via Syncthing

1. Create `<SYNCTHING_ROOT>/bin/` if absent.
2. Copy the contents of `samples/` (excluding `job-*.yaml`) into `bin/`.
3. On POSIX, `chmod +x bin/sample_calc bin/sample_calc.py`.
4. Syncthing syncs `bin/` to every node.

Or run `python scripts/install_sample_calc.py --syncthing-root PATH`.

`bin/` is not added to `.stignore`, so it syncs like `nodes/` and `jobs/`.

## Job profiles

Pre-built profiles in `backend/profiles/` for submitting via the cluster API:

| Profile ID | Description | Target runtime |
|---|---|---|
| `sample-calc-smoke` | Quick smoke test (< 5 s) | < 5 s |
| `sample-calc-cpu` | CPU 1-min using `torch`/`numpy` presets | ~60 s |
| `sample-calc-cpu-purepython` | CPU 1-min purepython (portable) | ~60 s |
| `sample-calc-gpu` | GPU 1-min using `torch` CUDA | ~60 s |

Submit via the profiles API endpoint or CLI using the profile ID. The
`working_dir: "."` resolves to `SYNCTHING_ROOT`, so `bin/sample_calc.py`
is found on all nodes.

## Exit codes

0 success, 1 invalid arguments, 2 backend unavailable, 3 computation error,
130 interrupted.
