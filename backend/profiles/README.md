# Profiles

Built-in run profiles for the Scientific Home Cluster. Each `*.yaml` file is a
`ProfileSpec` loaded by `backend/services/profile_service.py`.

## Other built-in profiles

- `cp2k-md` — CP2K ab initio MD.
- `gromacs-mdrun` — GROMACS molecular dynamics.
- `lammps-gpu` — LAMMPS molecular dynamics (GPU).
- `monte-carlo` — Monte Carlo simulation.
- `pytorch-train` — PyTorch training run.
- `qe-pwscf` — Quantum ESPRESSO SCF/relaxation.
- `rmc-profile` — RMCProfile reverse Monte Carlo.
- `sample-calc-smoke` — Quick smoke test (< 5 s).
- `sample-calc-cpu` — CPU sample calculation (~60 s).
- `sample-calc-cpu-purepython` — Portable CPU sample calculation (~60 s).
- `sample-calc-gpu` — GPU sample calculation (~60 s).
- `vasp-std` — Standard VASP relaxation.
- `vasp-gpu` — VASP GPU (OpenACC).

## VASP (Docker)

The `vasp-cpu-docker` and `vasp-gpu-docker` profiles run VASP inside the
`alzrse/vasp_gpu:6.3` image.

Dockerfiles for building your own VASP image live in a separate project:
https://github.com/AlZrSe/vasp-docker

### Build your own image

1. Clone the project:
   `git clone https://github.com/AlZrSe/vasp-docker.git`
2. Follow that repository's README to obtain the VASP source and place it where
   the Dockerfile expects it.
3. Build the image (CPU or GPU variant as documented there):
   `docker build -t my-vasp:6.3 .`
4. Point a profile at your image by replacing `alzrse/vasp_gpu:6.3` in the
   profile command with `my-vasp:6.3` (or create a custom profile via the UI).