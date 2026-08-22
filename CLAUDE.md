# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The IDIA MeerKAT pipeline (`processMeerKAT.py` and friends): a SLURM-orchestrated radio-interferometric
calibration pipeline for MeerKAT data (cross-calibration, self-calibration, science imaging), built on
CASA6 (`casatools`/`casatasks`/`casampi`). Originally built for the Ilifu cluster; the `HI-pawsey` branch
is an in-progress port to Pawsey's Setonix (Cray/HPE Shasta, Slurm), currently being adapted for narrow-SPW
HI/spectral-line workflows rather than the original multi-SPW continuum default.

Branches: `master`/`dev` (upstream Ilifu), `HI-dev` (HI-focused work off dev), `HI-pawsey` (Pawsey port,
active), `casa6`, `selfcal_dev`.

## Environment setup

```bash
source setup.sh   # adds processMeerKAT/ to PATH and PYTHONPATH (also sets SINGULARITYENV_PYTHONPATH)
```

No package manager, build step, or automated test suite — this is a script-based pipeline validated by
actually running it against a MeasurementSet.

## Running the pipeline

```bash
# 1. Build a config file from a raw MS (extracts field IDs, computes resource defaults, etc.)
processMeerKAT.py -B -C myconfig.txt -M mydata.ms [-P for polarization] [-2 for selfcal] [-I for science imaging]

# 2. Generate SLURM job scripts (submit_pipeline.sh + one .sbatch per script + summary/killJobs/findErrors helpers)
processMeerKAT.py -R -C myconfig.txt

# 3. Submit
./submit_pipeline.sh
```

`myconfig.txt` is the user-facing, persistent config (edit and re-run `-R` to regenerate job scripts).
`.config.tmp` is the *runtime* copy that individual pipeline scripts read and progressively rewrite as
they run (e.g. `partition.py` updates `[data] vis` to point at the newly-created MMS; `selfcal_part1.py`/
`selfcal_part2.py` advance `[selfcal] loop`). **`-R`/`submit_pipeline.sh` both reset `.config.tmp` from
`myconfig.txt`** — if you're manually resuming a partially-run pipeline (re-submitting from partway
through), restore `.config.tmp`'s progressed `[data] vis` / `[run] orig_vis`/`crosscal_vis`/`loop` fields
after regenerating, or the resumed scripts will silently operate on stale/original data.

Validate a code change with a syntax check before regenerating job scripts (no compiled build step):
```bash
python3 -c "import ast; ast.parse(open('processMeerKAT/processMeerKAT.py').read())"
```

## Architecture

### Config-driven, not code-driven

Almost all pipeline behaviour is controlled by `myconfig.txt`, parsed via `config_parser.py`
(`parse_config`, `get_key`, `overwrite_config`). Sections: `[data]`, `[fields]`, `[slurm]`, `[crosscal]`,
`[selfcal]`, `[image]`, `[run]` (internal/progress state). `default_config.txt` is the template `-B` copies
from; `default_cont_sofmask.txt` is a separate SoFiA parameter template (SoFiA's real defaults are
hard-coded in its own `Parameter.c` — this file is just a starting point for users' own SoFiA configs).

### The `[slurm] scripts` list is the pipeline's execution plan

`myconfig.txt`'s `scripts` (plus `precal_scripts`/`postcal_scripts`, run before/after the main per-SPW
loop when `nspw > 1`) is a list of `(script_name, threadsafe, container_override)` tuples. This list *is*
the pipeline DAG — `processMeerKAT.py -R` walks it in order via `write_jobs()`/`write_sbatch()` and emits
one `.sbatch` file per entry, chained with `sbatch -d afterok:...` in `submit_pipeline.sh`. Actual script
implementations live in `crosscal_scripts/`, `selfcal_scripts/`, `aux_scripts/`, or top-level
(`read_ms.py`, `validate_input.py`, `science_image.py`).

The `threadsafe` flag decides `ntasks-per-node`: `True` gives the script the full configured
`ntasks_per_node` (multi-task `srun`, i.e. real MPI parallelism via `casampi`); `False` forces a single
task. This is a *correctness* flag, not just a performance one — a script must actually cooperate across
concurrent tasks (via CASA's MPI client/server model, `casampi`) to be safely marked `True`. Whether a
CASA task needs an MMS to parallelize matters here: `mstransform`/`flagdata`/`split`-family tasks split
work across an MMS's existing sub-MSs, while `tclean(parallel=True)` parallelizes over its own major/minor
cycle structure and works on a plain MS *or* MMS.

`write_sbatch()` (in `processMeerKAT.py`) derives `--cpus-per-task`/`--mem` per script from a mix of
hard-coded heuristics (substring-matched on script name: `'tclean'`, `'selfcal'`, `'image'`, `'flag'`,
`'partition'` all get elevated CPU counts) and the configured `[slurm] mem`/`ntasks_per_node` — this is
the "spaghetti" the user wants detangled during the Pawsey refactor (see below); understand it script by
script rather than assuming a single clean rule.

### SPW-level parallelism is separate from MPI parallelism

When `[crosscal] nspw > 1`, `partition`-named scripts get a SLURM `--array` job (one array task per SPW
directory), running the pipeline's calibration steps independently per spectral window — completely
orthogonal to the `threadsafe`/MPI mechanism above. `precal_scripts` (e.g. `calc_refant.py`,
`partition.py`) run once at the top level before this fan-out; `postcal_scripts` (e.g. `concat.py`,
`selfcal_part1.py`/`selfcal_part2.py` repeated per `nloops`, `run_sofia.py`, `science_image.py`) run once
after SPW outputs are concatenated back together. `bookkeeping.py` provides the shared helpers scripts use
to read config, resolve per-loop filenames (`get_selfcal_args`), and run with consistent logging/error
handling (`run_script`).

### Pawsey-specific container/MPI layer (`HI-pawsey` branch, top of `processMeerKAT.py`)

The pipeline calls every script via `singularity exec <container> python3 <script> --config .config.tmp`
(built in `write_command()`). On Setonix with the `idianext.sif` container, several fixes were needed
*without rebuilding the container* — each is a lookup table keyed by container path, all applied in
`write_command()`:

- `CONTAINER_PYTHON`: idianext.sif's real Python (with `casatasks` etc.) is a venv at
  `/opt/venv/bin/python3`, not the bare `python3` on `PATH`.
- `CONTAINER_ENV` (via `singularity exec --env`): `LD_PRELOAD` for an OpenSSL SONAME conflict,
  `PYTHONPATH` for a source-built `mpi4py` + `casampi==0.6.0` override living outside the container (at
  `containers/idianext_mpi4py`, not in this repo), and `OMPI_COMM_WORLD_RANK=$PMI_RANK` because
  `casampi`'s MPI-init gate only recognizes OpenMPI's env var, not Slurm's PMI/PALS.
- `CONTAINER_BINDS` (via `--bind`): `/var/spool/slurmd`, which Cray's PALS launcher needs for its
  per-job MPI rendezvous state and which isn't in the site's default bind list.
- `CONTAINER_PREPEND_ENV`: for variables (e.g. `LD_LIBRARY_PATH`) where the site's own singularity module
  *also* sets a `SINGULARITYENV_<VAR>` value that must be preserved, not clobbered — emitted as a
  host-side `export SINGULARITYENV_<VAR>="<addition>:$SINGULARITYENV_<VAR>"` line before the `singularity
  exec` call, rather than `--env`, since `--env VAR=...` replaces rather than composes with the site's
  value (confirmed empirically: it silently drops Cray's MPI/fabric library paths).

`selfcal_part1.py` is currently the only script using `#SBATCH --exclusive` (needs the whole node's
memory for large wide-field images); Setonix's real (non-`--test-only`) admission control for exclusive
jobs additionally requires `--ntasks-per-node` to evenly divide the node's physical core count — the code
rounds down to the nearest power of two for this one script rather than using the configured
`ntasks_per_node` directly, since `sbatch --test-only` does not surface this constraint.

`MEM_PER_CPU_MB_SHARED`/`DEFAULT_MEM_GB` implement Setonix's shared-partition memory model: `--mem` is
capped to (or, for single-task scripts, drives extra otherwise-idle cpu reservation to reach) a multiple
of the cluster's per-core memory ratio, since requesting more memory than a job's core count justifies
gets it rejected outright by `sbatch` on this system (a different failure mode than simply queuing).
