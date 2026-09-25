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
renamed from `[image]`), `[contsub]` (uvcontsub.py's fit params — `fitspw`/`target_velocity` auto-estimated
at `-B` time when left blank, see below), `[hi_image]` (HI/spectral-line cube
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
this pipeline's accumulated knowledge of one mode: `chanbin`/`nspw` are a property of the mode + total
delivered bandwidth, not of HI imaging specifically, so `read_ms.py` defaults them from the identified mode
unconditionally (every run, not just `-H`/`--contsub` ones) — only `imspw_mhz` (narrowing `[hi_image] imspw`
and `[crosscal] spw` to a local window around the line) stays specific to `[-H --hi_image]`, since a plain
continuum run doesn't want its spw narrowed to one line's band. Each mode entry also carries a `slurm` dict
(`{pipeline_role: {'nodes': N, 'ntasks_per_node': M}}`) of per-script SLURM resource requests learned by
actually profiling a real run against that mode — applied regardless of `-H`, since e.g. `selfcal_part1`
runs whenever `[-2 --do2GC]` is set. `slurm_config_registry.py` is the resolution layer `write_jobs()`
consults per script (`get_override(pipeline_role, mode_name)`) — it holds no per-script numbers of its own,
purely a lookup by the persisted mode name into `correlator_modes.MODES[...]['slurm']`. A script with no
profiled data for the identified mode (or no mode identified at all) is completely unaffected, falling back
to the run's plain configured `[slurm] nodes`/`ntasks_per_node`. Add a new mode's `MODES` entry only once
its resource needs are actually profiled against a real run — never guess a number in to get a run started.

### `[contsub]` fitspw auto-estimation

`contsub_utils.py` (CASA-free, unit-testable) converts between frequency and velocity via the radio
convention (`v = c(f₀-f)/f₀` — exact/linear in frequency, the appropriate convention for HI work, unlike
optical convention which isn't linear and diverges more at higher velocity). `read_ms.py` uses it at `-B`
time, whenever `[-H --hi_image]` or `--contsub` is set, to fill in `[contsub] target_velocity` (left blank:
derived from `[-F --centralspw]`/the MS's own centre frequency, i.e. assumes the band is already centred on
the line) and then `fitspw` itself (left blank: an MSSelection string excluding a `fitspw_vwidth`-wide
velocity window around that line centre, scoped to a local ±10MHz band — mirrors `badfreqranges`' own
multi-range `'*:lo~hiMHz,*:lo~hiMHz'` convention, see `flag_round_1.py`'s `do_pre_flag()`). Either key left
non-blank by the user is never overridden. `[contsub]` keeps its own `restfreq` (rest-frame frequency of the
*line*, e.g. HI's 1420.406MHz — a physical constant, not tied to any galaxy's velocity) rather than reading
`[hi_image]`'s, since `--contsub` can run standalone without `[hi_image]` ever being touched.

### Multi-track combining (`--combine`)

`processMeerKAT.py --combine <dir> [-H]` combines several already-run tracks' output (sibling `P<N>`
run directories under `<dir>`) into one MS via `virtualconcat`, for imaging as a single, deeper dataset.
Deliberately standalone — not part of the `-B`/`-R` DAG (no per-run-directory scoping exists for a tool
that reads across multiple independent run directories) and not registered in `script_registry.py`.
`write_combine_jobs()` (in `processMeerKAT.py`) reuses the same DAG-generation machinery `-R` uses for a
single track — `write_sbatch()` (including `slurm_config_registry`'s per-correlator-mode resource
overrides) and `write_master()` (including `expand_hi_combo_scripts()`'s per-stage/per-combo replication
and the summary/killJobs/findErrors/etc. helper scripts) — to build a full `submit_pipeline.sh` chaining
`combine_tracks.py` → `hi_image.py`/`hi_sofia.py` (or `science_image.py`/`cont_sofia.py` for continuum),
minus the crosscal/selfcal machinery that doesn't apply to an already-calibrated, already-concatenated MS
— `combine_tracks.write_combined_config()` writes no `[crosscal]` section at all (previously wrote a
`spw=''`/`nspw=1` stub purely to satisfy `bookkeeping.run_script()`'s then-unconditional read of those two
keys; fixed live, 2026-09-18 — `run_script()` now tolerates a config with no `[crosscal]` section,
defaulting the same `('', 1)` a stub would have held, since those values only ever mattered for
broadcasting a failure's `continue=False` into per-SPW subdirectories, meaningless when there's no
per-SPW fanout to broadcast to). `write_combine_jobs()` itself bypasses `write_jobs()`/
`get_config_kwargs(config, 'crosscal', CROSSCAL_CONFIG_KEYS)` entirely for the same reason — that call
requires every `CROSSCAL_CONFIG_KEYS` key present, which a combined config deliberately never has.
`[data] vis` and `[run] hi_contsub_vis`/`post_selfcal_vis` must both be bare filenames (not
`os.path.join`'d with the output directory) — every script that reads them runs with cwd already at that
output directory, and embedding the directory a second time silently doubles it up.

**`--combine -H`'s `[hi_image]` section is built from real derived defaults, never copied from a track.**
`write_combined_config()` seeds `[hi_image]` from `default_config.txt`'s own shipped template — exactly
what a fresh `-B` build starts from — not from any one track's `myconfig.txt`. Confirmed live (2026-09-18,
N4064's `M2`): the earlier design copied `[hi_image]` wholesale from `tracks[0]`, which broke two ways —
`tracks[0]` isn't guaranteed to even have a `[hi_image]` section (three of N4064's four tracks were only
ever built with plain `-B`, not `-B -H`), and even when it does, the copied `imspw` reflects only that one
track's own `-F`/MS assumptions, never independently verified against the actual combined data.
`run_combine()` (in `processMeerKAT.py`) fills `imspw`/SoFiA-kernel overrides back in right after, from two
different kinds of real facts: `[run] correlator_mode` is *cross-checked* directly against every combined
track's own already-recorded value (present unconditionally on every track, since `read_ms.py` writes it
at every `-B` regardless of `-H` — raises if tracks disagree, rather than trusting one); then a short-lived
CASA call (`combine_hi_defaults.py`, invoked synchronously the same ad hoc non-sbatch way `read_ms.py`'s
own `-B` field extraction is) derives the centre frequency from one of the real per-track source MSs
(only when `[-F --centralspw]` wasn't given) and computes `imspw`/SoFiA overrides via
`correlator_modes.compute_imspw()` (also used by `read_ms.py`'s own `-B -H` block, factored out so both
share it). **The mode itself is never re-identified from that source MS's own spectral facts** — it's
already a `.contsub` output, through `[crosscal] chanbin` averaging by that point, so its `ChanWid` no
longer matches `identify_mode()`'s native-channel-width table (confirmed live: 6.531kHz observed vs the
3.265kHz `32K_NE107M` expects — exactly the 2x `chanbin=2` already baked in) — hence reading the
cross-checked name back from config instead of re-deriving it from that MS.

**`virtualconcat`'s own `keepcopy=True` is broken for any `vis` path containing a `/`** — confirmed live
via a real `FileNotFoundError` mid-combine that briefly stranded a real 459GB per-track `.contsub` output
(moved but not restored) before crashing. Its backup dance does `shutil.move(elvis, tempdir)` (lands at
`tempdir/<basename>`, since `shutil.move()` to a directory target uses `os.path.basename`) then tries to
restore via `shutil.copytree(tempdir+"/"+elvis, elvis, True)` — a bare string concat of the *full*
original path, not its basename. Only correct when every `vis` entry is already a bare filename in `cwd`
— never true here, since each track's `.contsub` lives in its own directory. `combine_tracks.py` works
around this entirely: makes its own disposable local copies of each track's `.contsub` first, then calls
`virtualconcat(..., keepcopy=False)` (skipping CASA's broken backup block completely) — one extra copy
pass, but the true per-track originals are never touched by the broken code path.

### HI/continuum cube imaging: PSF/residual/mask reuse on resume, and `nmajor`

`image_engine.run_stage()` (shared by `hi_image.py`/`science_image.py`) is idempotent at the whole-stage
level (skips entirely if `<imagename>.image` already exists) but also reuses partial `tclean` state
*within* an incomplete stage, rather than always restarting from scratch — confirmed live this matters a
lot: a stage's `WPConvFunc::findConvFunction` (PSF/w-projection derivation) step alone took ~36 minutes
on one HI cube, entirely wasted on every manual resume before this existed, since it's independent of
`niter`/deconvolution progress.

- `calcpsf=False` whenever `<imagename>.psf`/`.sumwt` already exist.
- `calcres=False` whenever *both* `<imagename>.residual` and `.model` already exist — resumes minor-cycle
  work directly instead of recomputing the initial residual (a real gridding pass) from scratch.
- When resuming (`calcres=False`) and `<imagename>.mask` already exists too, `mask=''` is passed instead
  of re-supplying the FITS/CASA-image mask a second time — `tclean` itself refuses a fresh `mask=`
  argument once `.mask` already exists ("Please either reset mask='' to reuse the existing mask, or
  delete `<imagename>.mask` before restarting"), found on the very first live resume attempt.
- All three checks are pure `os.path.exists()`, same "clear stale output to force a redo" convention as
  every other idempotency check in this pipeline: if imaging params (mask, weighting, robust,
  wprojplanes, ...) changed since a prior attempt, delete the relevant `<imagename>.*` first.
- `[hi_image] nmajor` (default `-1`, unlimited, threaded through to `tclean()`) caps major cycles per
  call. Confirmed live: once most/all channels in a cube converge, `tclean` has no stopping criterion for
  "nothing left to clean" — it keeps re-gridding the *entire* dataset every major cycle for zero benefit,
  indistinguishable from real progress without reading individual `SDAlgorithmBase::deconvolve` log lines
  (`iters=0->0 ... Reached cyclethreshold` on every channel, every cycle). A finite cap turns that wasted
  tail into a graceful return with a usable (if possibly short-of-convergence) image, instead of a
  walltime `SIGKILL` with nothing exported.

**SoFiA output placement**: `hi_sofia.py`/`cont_sofia.py`'s masking pass must write its mask directly into
the combo/stage's own top-level directory (`resolve_mask()` hard-codes `<imagename_fn(stage-1)>_mask.fits`
there for the *next* stage's `mask='prev'` lookup) — but the *final* pass's outputs
(mask/catalog/moments/cubelets/noise/plots) go into `<combo_dir>/fincubes/`, alongside the rebinned,
beam-collapsed, velocity-converted science cube (`image_engine.finalize_stage()`'s own export) they were
derived from, not scattered into the combo directory directly.

**A stage's `threshold` can be left undefined (omitted, `None`, or `''`) for any stage after the first** — it is
then derived by `image_stages.resolve_threshold()` as 1.3x the median of the previous stage's SoFiA noise
spectrum (`<combo_dir>/stage<N-1>_noise.txt`, written by the masking pass because `default_hi_sofmask.txt`
sets `output.writeNoise = true`), as a CASA `mJy` quantity. The median is over non-zero channels only —
SoFiA writes exactly 0 for channels with no data (the empty channels beyond `imspw`, see below; N4064's
noise file had 345 of them) — and a median rather than a mean so channels with bright line emission don't
inflate it. Stage 0 must still set one explicitly (nothing precedes it), and a missing noise file raises
with a pointer to `output.writeNoise` rather than falling back silently. Used by both `hi_image.py` and
`science_image.py` (shared code), but only exercised against HI cubes; a threshold set explicitly is
returned untouched, so existing configs are unaffected.

**Each combo's output directory is named from its weighting, not its position.** `image_stages.combo_dirname()`
gives `hi_combo_r<robust>` plus `_t<uvtaper>` when a taper is set — `{'robust': 1.0}` → `hi_combo_r1`,
`{'robust': 0.0}` → `hi_combo_r0`, `{'robust': -0.5}` → `hi_combo_rm0p5` (`m` for minus, `p` for the decimal
point), `{'robust': 0.0, 'uvtaper': '40arcsec'}` → `hi_combo_r0_t40arcsec`. Position-based `hi_combo<N>` names
silently changed meaning whenever a combo was added, removed or reordered. `hi_image.py` and `hi_sofia.py` both
resolve the directory through `image_stages.combo_dirnames(hi_combos)`, which raises if two entries would share
one (same robust and uvtaper). Directories from before this convention (`hi_combo0`, ...) need renaming by hand
to match if a later step should find them — nothing migrates them.

**`[hi_image] cell` can be a per-combo list, not just one shared string.** Different `robust`/`uvtaper`
weightings in `hi_combos` change the synthesized beam, so a single cell size doesn't suit every combo once
more than one is configured. `hi_image.py` reads `cell` directly (not via `config_parser.validate_args()`,
str/int/float/bool only) and accepts either one string (unchanged, shared across every combo) or a list
with one entry per `hi_combos` entry, indexed positionally by `combo` — errors out if the lengths don't
match rather than silently misapplying the wrong cell size.

**`hi_combos` is work in progress — only `cell` varies per combo so far; run combos one at a time for
now (deferred, 2026-09-21).** `imsize` and each stage's `threshold` (and `niter`) are still single values
shared by every combo, but they don't suit differently-weighted combos either: a robust 0.0 combo has a
higher noise floor than robust 1.0, so the shared stage0 `threshold` (0.6mJy) sits below its noise and
each channel chases noise instead of converging (confirmed live on N4064's robust 0.0 combo0: 21h+ into
stage0 with 600+ "Possible divergence" warnings, vs 5h36m for the robust 1.0 combo at the same `niter`),
and a finer/coarser `cell` changes how much sky a fixed `imsize` covers. Proper support needs `imsize`
and per-stage `threshold`/`niter` to become per-combo lists too (same positional-indexing convention as
`cell`, validated against `len(hi_combos)`). Until then, run multiple combos sequentially (default
`--combine` behaviour), editing `threshold`/`imsize` by hand between combos as needed. `--parallel_combos`
exists (independent per-combo config copies and job chains, see `write_combine_jobs_parallel_combos()`)
but has only been exercised by generation-time tests, never run on real data, and inherits the same
shared-`threshold`/`imsize` limitation — don't rely on it until the per-combo parameters above land.

**A stage's `mask` can be `'auto-multithresh'`, not just `None`/`'prev'`** — lets CASA's own automasking
algorithm derive/refine its mask internally each major cycle (`usemask='auto-multithresh'`), instead of no
mask or a user-supplied SoFiA island mask. `image_stages.resolve_mask()` returns the literal string
`'auto-multithresh'` as a sentinel (not a real path); `image_engine.run_stage()` special-cases it into
`usemask` rather than trying to import it as a FITS file. Added 2026-09-22 specifically for stage 0 (which
has no previous stage to reference via `'prev'`, and previously only supported a fully blind `None` clean):
a real blind stage0 (N4064, robust 0.0) found 2819 positive vs 2924 negative S+C candidates across the
whole 2048×2048 field — too noise-dominated for SoFiA's reliability step to call anything reliable, even
though real sources were visible by eye — the hope is that constraining cleaning to likely-real-emission
regions finds fewer noise-level candidates. Not yet verified against a real run.

**Known gap, not yet fixed: HI cubes can have empty channels beyond the requested `imspw` window.**
Confirmed live (2026-09-18, N4064): a real `stage1.image` came out with 1263 channels (6.530kHz each,
matching `chanbin=2`) spanning 1409.161–1417.403MHz — visibly wider than, and offset from, any `imspw`
window this pipeline has configured, with essentially all of the extra (empty, no data gridded) channels
on one edge, not padded symmetrically. Root cause: `image_engine.run_stage()`'s `tclean()` call passes
`spw=imspw` (a frequency-range *selection* string) but never sets `nchan`/`start`/`width` — those stay at
CASA's own defaults. Combined with `specmode='cube'` + `outframe='bary'`/`veltype='optical'` (this MS's
own channels are natively in a different, non-BARY frame — confirmed via `msmd.chanfreqs()` vs the BARY-
frame `imspw` numbers), CASA's automatic channelization does not tightly clip the output grid to `spw=`'s
MHz bounds — it derives the grid from the selected visibilities' own native channelization, then
reprojects into the requested `outframe` once, which isn't guaranteed to land flush with the requested
window. Standard CASA guidance for spectral-line cube imaging is to always pass `start`/`width`/`nchan`
explicitly rather than relying on `spw=` selection + frame-conversion defaults, for exactly this reason.
Not yet fixed here — would need `start`/`width`/`nchan` computed from `imspw`'s bounds and the mode's real
channel width and threaded through `image_engine.run_stage()`'s `tclean()` kwargs.

### SoFiA spectral kernel/linker parameters are channel-unit, and must match the mode's real channel width

`scfind.kernelsZ` (S+C finder spectral smoothing) and `linker.radiusZ`/`linker.minSizeZ` (friends-of-
friends merging/size thresholds), in `default_hi_sofmask.txt`, are expressed in **channel units** (SoFiA-2
User Manual), tuned for ~5.6km/s/channel. A correlator mode with a different effective channel width
(native `ChanWid` × `chanbin`) needs these rescaled to match the same real line-width intent, or SoFiA
chronically under-detects — confirmed live (N4064, `32K_NE107M`+`chanbin=2`, ~1.4km/s/channel, a clean 4x
mismatch): the unscaled defaults found only 3 sources running SoFiA standalone against a real combined-
track cube; scaling `kernelsZ`/`radiusZ`/`minSizeZ` by that same 4x (kernelsZ rounded to the nearest odd
values SoFiA requires) found 18, including the known target at its correct catalogued position.
`correlator_modes.py`'s `MODES` entries can carry `sofia_kernelsZ`/`sofia_linker_radiusZ`/
`sofia_linker_minSizeZ`, applied by `read_ms.py` at `-B` time to both `sofia_mask_params` and
`sofia_final_params` — same mode-driven-default pattern as `chanbin`/`nspw`/`imspw`, and equally only
meaningful for that mode's own `chanbin` default (revisit if `chanbin` is ever overridden). **Only fires
on a real `-B` run** — a config built via `combine_tracks.py`'s own `[hi_image]` template-copy (not a
real `-B` against an MS) never goes through `read_ms.py` at all, so it needs the same override applied by
hand if the template it copied from predates this mechanism.

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

**Every one of the `CONTAINER_ENV` fixes above is compensating for a stale container build, not an
inherent container limitation — confirmed live (2026-09-17).** `containers/spack-idia-fixed.def`'s
`%post` section (last updated 2026-08-22) already bakes in a source-built `mpi4py`, `casampi==0.6.0`, an
`OMPI_COMM_WORLD_RANK` shim (`91-ompi-rank-shim.sh`, more robust than the pipeline's own — it also falls
back to `SLURM_PROCID`), and an OpenSSL `LD_PRELOAD` shim (`91-openssl-preload.sh`, discovering the spack
hash dynamically rather than hardcoding it) as part of the container's own baked-in environment. But the
*deployed* `idianext.sif` (built 2026-05-24, three months *before* that def-file update) has none of
this: confirmed live that it has no `mpi4py` installed at all, `casampi` is still the buggy `0.5.9` (not
`0.6.0`), and neither `91-*.sh` script exists in its `/.singularity.d/env/`. The def file describes a
fixed recipe that was apparently never actually built into the image people are running — every
`CONTAINER_ENV`/`_IDIANEXT_MPI4PY_DIR`/`_IDIANEXT_OPENSSL_LIB` workaround in `processMeerKAT.py` exists
to paper over that gap, not because the container is fundamentally incapable of this. **Once `idianext.sif`
is rebuilt from the current def file** (a support-request-worthy ask, not something fixable from this
repo), `env['LD_PRELOAD']`, `env['OMPI_COMM_WORLD_RANK']`, and the entire `_IDIANEXT_MPI4PY_DIR` external
mpi4py override (plus its `PYTHONPATH` prefix) should all become removable — `binds=['/var/spool/slurmd']`
(the def file's own `%help` explains why this specifically can *never* be baked in: it's a per-job
directory that doesn't exist until the Slurm job starts) and `PYTHONPATH`'s `SCRIPT_DIR` entry (inherently
pipeline-specific, not a container concern) are the only pieces of this table with no container-side fix
possible. `prepend_env['LD_LIBRARY_PATH']` for `/opt/casacore/lib` **now has a matching container-side
fix too** (added 2026-09-17, ahead of the rebuild request specifically so it gets tested in the same pass
as the others): a third env-shim script, `92-casacore-libpath.sh`, mirroring the OpenSSL fix's own
dynamic-discovery pattern (`spack location -i casacore` + `find` for the actual `.so`, not a hardcoded
`lib`/`lib64` guess) — **not yet verified against a real build** (no ability to build/run the container
from here), so don't drop the pipeline's own `prepend_env` for this until a rebuilt image is confirmed to
have it working.

`selfcal_part1.py` is currently the only script using `#SBATCH --exclusive` (needs the whole node's
memory for large wide-field images); Setonix's real (non-`--test-only`) admission control for exclusive
jobs additionally requires `--ntasks-per-node` to evenly divide the node's physical core count — the code
rounds down to the nearest power of two for this one script rather than using the configured
`ntasks_per_node` directly, since `sbatch --test-only` does not surface this constraint.

`MEM_PER_CPU_MB_SHARED`/`DEFAULT_MEM_GB` implement Setonix's shared-partition memory model: `--mem` is
capped to (or, for single-task scripts, drives extra otherwise-idle cpu reservation to reach) a multiple
of the cluster's per-core memory ratio, since requesting more memory than a job's core count justifies
gets it rejected outright by `sbatch` on this system (a different failure mode than simply queuing).
