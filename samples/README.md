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
| purepython | 128 | 0.1304 | 460 | triple loop |
| torch (CUDA) | 4096 | to be profiled | 2000 (initial) | no CUDA on dev host; use `--calibrate` |

## Distribution via Syncthing

1. Create `<SYNCTHING_ROOT>/bin/` if absent.
2. Copy the contents of `samples/` (excluding `job-*.yaml`) into `bin/`.
3. On POSIX, `chmod +x bin/sample_calc bin/sample_calc.py`.
4. Syncthing syncs `bin/` to every node.

Or run `python scripts/install_sample_calc.py --syncthing-root PATH`.

`bin/` is not added to `.stignore`, so it syncs like `nodes/` and `jobs/`.

## Exit codes

0 success, 1 invalid arguments, 2 backend unavailable, 3 computation error,
130 interrupted.
