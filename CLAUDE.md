# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The IDIA MeerKAT pipeline (`processMeerKAT.py` and friends): a SLURM-orchestrated radio-interferometric
calibration pipeline for MeerKAT data (cross-calibration, self-calibration, science imaging), built on
CASA6 (`casatools`/`casatasks`/`casampi`). Originally built for the Ilifu cluster; the `HI-pawsey` branch
is an in-progress port to Pawsey's Setonix (Cray/HPE Shasta, Slurm), currently being adapted for narrow-SPW
HI/spectral-line workflows rather than the original multi-SPW continuum default.

Branches: `master`/`dev` (upstream Ilifu), `HI-dev` (HI-focused work off dev), `HI-pawsey` (Pawsey port,
active), `pawsey-refactor` (architectural refactor of the Pawsey port, forked off `HI-pawsey`'s tip; see
`REFACTOR_PLAN.md` on that branch for the phased plan and current progress before starting work there),
`casa6`, `selfcal_dev`.

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

### Resuming a partially-run pipeline mid-chain

There is no dedicated resume flag (confirmed against the upstream docs at
[idia-pipelines.github.io/docs/processMeerKAT/Advanced-Usage](https://idia-pipelines.github.io/docs/processMeerKAT/Advanced-Usage/)):
"the pipeline is generally not designed to run twice within the same directory." The documented — and
only — approach is manual: diff the configs, edit, and resubmit specific steps yourself. What follows is
the concrete recipe worked out in practice (e.g. resuming `HI_p1`'s selfcal loops after an OOM, and again
after fixing a script-ordering bug), not upstream documentation.

**Never re-run the full `submit_pipeline.sh` to resume mid-chain.** It unconditionally starts from
`partition.sbatch` (or `validate_input.sbatch` if `precal_scripts` is empty) and re-submits the *entire*
chain, including per-SPW crosscal — there's no way to tell it "start from step N." If crosscal (or any
earlier stage) already succeeded, running it again wastes hours re-doing finished work at best, and can
silently diverge from already-completed state at worst.

**The actual recipe**:
1. `diff myconfig.txt .config.tmp` — this is the ground truth for what's actually progressed (typically
   `[data] vis`, `[selfcal] loop`, `[run] crosscal_vis`/`orig_vis`/`timestamp`). Note every difference
   before touching anything else, since `-R` is about to erase `.config.tmp`.
2. If a code change (not just a config value) is needed, make it in `processMeerKAT.py`/scripts as normal,
   syntax-check it, and verify it — **but note `tools/golden_diff.sh`'s fixture config uses `nspw=1`
   (`write_master()`'s code path)**. A change specific to `write_spw_master()` (the `nspw>1` path, e.g. the
   `expand_selfcal_loop_scripts(handle_run_sofia=...)` bug found and fixed in this branch) will show
   `golden_diff.sh` as clean even when broken, because the fixture never exercises that function. Verify
   `nspw>1`-specific logic with a standalone script that imports the relevant function directly and asserts
   on its output (see git history around the `handle_run_sofia` fix for the pattern) — never trust
   `golden_diff.sh` alone for `write_spw_master()`-only changes.
3. Update whatever `myconfig.txt` values the resume needs (e.g. `[slurm] nodes`/`ntasks_per_node` for a
   different resource request going forward — this is a single global value with no per-script override,
   see below, so weigh side effects on other `threadsafe` scripts before bumping it).
4. Run `processMeerKAT.py -R -C myconfig.txt`. This regenerates every `.sbatch` file and
   `submit_pipeline.sh` fresh from the current code + `myconfig.txt`, and (when `nspw>1`) unconditionally
   writes a **new** `[run] timestamp` and re-runs `spw_split()` — confirmed harmless: `spw_split()` only
   rewrites each SPW directory's own `myconfig.txt` copy (`spw`/`nspw`/`vis`/etc., all re-derived
   idempotently) and skips `os.mkdir` for directories that already exist; it never touches on-disk MS/MMS
   data. The new timestamp is likewise cosmetic (only used to name the `jobScripts/*_$DATE.sh` helper
   files below) — it doesn't affect `LOG_DIR` (always `logs/`) or job correctness.
5. Restore `.config.tmp`'s progressed fields from step 1's diff. Re-diff `myconfig.txt` against
   `.config.tmp` afterward — it should show *only* those fields differing, confirming nothing else leaked
   through.
6. Inspect the freshly generated `submit_pipeline.sh` (`grep`/`sed -n` around the relevant `#<script>.sbatch`
   comments) to confirm the job order matches what you expect — especially after a code change to the
   generation logic itself, this is the real end-to-end proof it worked, not just the standalone test from
   step 2.
7. Hand-build a small standalone script containing only the remaining `sbatch -d afterok:${allSPWIDs//,/:}
   --kill-on-invalid-dep=yes <script>.sbatch` chain, copied from the relevant tail of the regenerated
   `submit_pipeline.sh` — starting with a bare `sbatch <first-remaining-script>.sbatch` (no `-d`, since
   nothing before it needs to run). Submit that, not `submit_pipeline.sh`.
8. **Update the summary/kill/errors/etc. helper scripts.** `write_bash_job_script()` (called from
   `write_spw_master()`/`write_master()`) is what generates `jobScripts/allSPW_summary_$DATE.sh` (and
   `killJobs`/`findErrors`/`displayTimes`/`cleanup` siblings) and re-points the `allSPW_summary.sh` etc.
   symlinks — but that code only *writes bash text into `submit_pipeline.sh`*; it does not execute at `-R`
   time. It only actually runs (regenerating the files, updating the symlinks) when `submit_pipeline.sh`
   itself is *executed*. A hand-built resume script (step 7) bypasses this entirely by construction, so
   `./summary.sh`/`./allSPW_summary.sh` silently keep pointing at stale job IDs from the last real
   `submit_pipeline.sh` execution unless you update them yourself: edit the `sacct -j <ids>` line in the
   target file the `allSPW_summary.sh` symlink points to (`ls -la allSPW_summary.sh` to find it — its name
   embeds the `[run] timestamp` from the run that last executed `submit_pipeline.sh` for real, not
   necessarily the current one) with the complete real job ID history (`sacct -u $USER -S <run-start> -X
   --format=JobID,JobName,State,Start` filtered to the relevant script names is the reliable way to
   reconstruct this, rather than trusting memory). Do this after *every* manual resubmission, not just the
   first.

## Architecture

### Config-driven, not code-driven

Almost all pipeline behaviour is controlled by `myconfig.txt`, parsed via `config_parser.py`
(`parse_config`, `get_key`, `overwrite_config`). Sections: `[data]`, `[fields]`, `[slurm]`, `[cluster]`
(Setonix hardware facts/named partitions), `[crosscal]`, `[selfcal]`, `[cont_image]` (continuum imaging —
renamed from `[image]`), `[contsub]` (uvcontsub.py's fit params), `[hi_image]` (HI/spectral-line cube
imaging, independent of `[cont_image]` — see "HI cube imaging" below), `[run]` (internal/progress state).
`default_config.txt` is the template `-B` copies from; `default_hi_sofmask.txt` is a separate SoFiA
parameter template shared by HI and continuum imaging's masking/final passes (SoFiA's real defaults are
hard-coded in its own `Parameter.c` — this file is just a starting point, with per-pass overrides layered
on top from `[hi_image]`/`[cont_image]`'s own `sofia_mask_params`/`sofia_final_params`).

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
`script_registry.py`'s declared per-script properties (`cpu_intensive` — pulls `cpus-per-task` up to
`cpus_per_node/tasks`; `exclusive_node` — whole-node `--exclusive`, currently only `selfcal_part1.py`;
`long_partition` — force onto `[cluster] long_partition`), a couple of remaining script-name-matched
special cases for a script whose real profile doesn't fit the generic heuristic (`run_sofia`, `selfcal_part2`
— see their comments in `write_sbatch()`), and the configured `[slurm] mem`/`ntasks_per_node` — understand
it script by script rather than assuming a single clean rule.

### Correlator-mode-aware defaults and per-script SLURM resource requests

`correlator_modes.py` identifies the input MS's MeerKAT correlator mode from its own spectral facts (native
channel width — `ChanWid`, the one invariant that survives a delivered MS being only a spectral *subset* of
the mode's full native band, e.g. HI_p1's MS is 6127 channels/20MHz, not `32K_NE107M`'s full 32768/107MHz —
`nchan`/total bandwidth are NOT reliable identifiers for this reason). `read_ms.py` identifies the mode
unconditionally at `-B` time (every run, not just `-H` ones) and persists only the matched *name* into
`[run] correlator_mode` — no MS/`msmd` access exists at `-R` time to re-derive it. Each `MODES` entry is
this pipeline's accumulated knowledge of one mode: HI-imaging-specific spectral defaults (`chanbin`/`nspw`/
`imspw_mhz`, applied by `read_ms.py` only when `[-H --hi_image]` is set) *and* a `slurm` dict
(`{pipeline_role: {'nodes': N, 'ntasks_per_node': M}}`) of per-script SLURM resource requests learned by
actually profiling a real run against that mode — applied regardless of `-H`, since e.g. `selfcal_part1`
runs whenever `[-2 --do2GC]` is set. `slurm_config_registry.py` is the resolution layer `write_jobs()`
consults per script (`get_override(pipeline_role, mode_name)`) — it holds no per-script numbers of its own,
purely a lookup by the persisted mode name into `correlator_modes.MODES[...]['slurm']`. A script with no
profiled data for the identified mode (or no mode identified at all) is completely unaffected, falling back
to the run's plain configured `[slurm] nodes`/`ntasks_per_node`. Add a new mode's `MODES` entry only once
its resource needs are actually profiled against a real run — never guess a number in to get a run started.

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
