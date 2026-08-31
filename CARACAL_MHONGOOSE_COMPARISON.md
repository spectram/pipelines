# Comparison: our pipeline vs. CARACal / the MHONGOOSE data-reduction paper

Sources: de Blok et al. 2024 (MHONGOOSE survey I, [arXiv:2404.01774](https://arxiv.org/abs/2404.01774)),
§5 "Data reduction" (read in full); [CARACal](https://github.com/caracal-pipeline/caracal) v1.2.2
(README/pyproject/schema-file listing only — worker source code wasn't read, see caveats inline).
Compared against `processMeerKAT`/`pawsey-refactor` as it stands after this session's work.

## 1. Architectural differences

| | Our pipeline | CARACal | MHONGOOSE (paper's actual run) |
|---|---|---|---|
| Orchestration | CASA6 (`casatasks`/`casatools`/`casampi`) scripts, one Singularity container for CASA + one for SoFiA, chained via `sbatch -d afterok` | Stimela: each worker is one or more separately-containerized (Docker/Podman/Singularity) task calls | Uses CARACal as-is |
| Config | One flat `myconfig.txt` (`ConfigParser`, Python-literal values), sections like `[crosscal]`/`[selfcal]`/`[hi_image]` | One YAML file per run (`caracal -c config.yml`), one schema file per worker | — |
| Flagging | CASA `flagdata` only: manual clip → `tfcrop` (cal fields and target, different cutoffs) → `extend` | **AOFlagger** (`.rfis` strategy files, separate strategies per field/Stokes) is primary; CASA `flagdata`/tfcrop also used for some passes | AOFlagger on calibrators (Stokes Q for RFI); CASA `tfcrop` (binned to 100 channels) for time-domain flagging |
| Imaging | CASA `tclean` | **WSClean** | WSClean (self-cal + full cubes) |
| Self-cal masking | PyBDSF every loop, except the *last* loop swaps to SoFiA if `run_sofia.py` is wired in (this session's `mask_image()` clobber fix now makes that swap actually work) | SoFiA (paper: "SoFiA source finder ... as implemented in CARACal") | SoFiA-driven mask → image → sky-model loop, phase-only self-cal |
| Continuum subtraction | **Two passes, both already in place**: (1) `uvsub.py` — subtracts the final self-cal loop's sky model (populated into `MODEL` by `selfcal_part2.py`'s own `tclean(niter=0, savemodel='modelcolumn', restart=True)` predict call), via CASA's native `uvsub()`; (2) `uvcontsub.py` (CASA `uvcontsub`, polynomial fit to line-free channels) for the residual. `uvsub.py` predates this refactor (last touched in `aa3318b`, "added sofia masking to generate final continuum model for subtraction") and is still wired immediately before `uvcontsub.py` in `postcal_scripts` | Two passes: (1) transfer self-cal clean-component model + subtract via **Crystalball**, (2) residual polynomial fit via CASA `mstransform` | Same two-pass approach; step (1) is "one-third to one-half of the total processing time" |
| HI cube imaging | Single `hi_image.py`/`hi_sofia.py` stage chain per `[hi_image] hi_combos` entry (our own generalization of "N resoluts") | Dedicated `line_worker.py` (largest schema in the repo, 54KB) | 6 fixed robust/taper "standard resolutions," each imaged independently, **no common-beam step** (each resolution keeps its own distinct beam) |
| Cube masking | Single mask per stage, escalating `niter`/`threshold` across 2 stages | — | **Cascading mask chain**: lowest-resolution cube's final SoFiA-2 mask is regridded and reused as the seed mask for the next-higher resolution, repeated across all 6 resolutions |
| Velocity convention | **Optical** (`fincubes_postprocess.py`, matches `tclean(veltype='optical')`) | — | **Radio** (`v = c(ν0−ν)/ν0`) — MHONGOOSE's own explicit choice for the heliocentric regrid |
| Frame correction | Barycentric (`tclean(outframe='bary')`) | — | Heliocentric (very similar magnitude correction, different reference point) |
| Galactic HI exclusion | **Just added this session** (`badfreqranges += '1419.8~1421.3MHz'`) | — | Same exact window, quoted verbatim from the paper — confirms this session's addition matches the field's own practice |
| Deployment | Setonix (Slurm, Singularity), this refactor's own `script_registry.py`/`[cluster]` config | Docker/Podman/Singularity; documented Slurm install path exists for ILIFU but no evidence of built-in multi-node array/MPI orchestration — reads as single-host, sequential-per-worker | ASTRON's own 4-node/128-core/1TB-RAM cluster, not Slurm |
| License / maturity | This repo, active refactor | GPL-2.0, "Production/Stable," active CI/pre-commit, v1.2.2 | — |

**Caveat on the CARACal side of this table**: only `README.rst`/`pyproject.toml`/`ruff.toml`/`stimela-master.txt`
and the repo's file/schema *names* were actually read — no worker source code. Everything above
attributed to CARACal specifically (vs. the paper) is either quoted from the README or corroborated
independently by the paper's own text.

## 2. Simple optimizations to adopt first (no new dependencies)

Ranked by (impact × ease). All of these can be built with CASA tasks / numpy already in use elsewhere
in this codebase — no new container, no new pip package.

### 2.1 Baseline-length cut for primary-calibrator delay/bandpass/gain (the example given) — ✅ implemented
> *"the primary calibrator was used to derive the delay, gain and bandpass calibration using
> baselines longer than 150m"*

New `[crosscal] calib_uvrange` key (default `''`, no cut — current behaviour preserved), threaded
as `uvrange=calib_uvrange` into all three `xx_yy_solve.py` calls (delay/K, bandpass/B, gain/G).
Applied uniformly across all three by design, not scoped only to whichever field the paper calls
"primary" — our pipeline's delay solve uses `fields.kcorrfield` (the *phase* calibrator, per
`bookkeeping.get_field_ids()`), not `fields.bpassfield` (our closest match to their "primary"), so
replicating their exact primary/secondary split onto our different field-assignment scheme wasn't
a clean fit. The underlying rationale (avoiding short-baseline systematics/resolved calibrator
structure) applies regardless of which field is being solved, so one uniform knob was chosen
instead. Set `[crosscal] calib_uvrange = '>150m'` to actually use it (mirrors the paper's own
value) — off by default.

### 2.2 Box-car smoothing of the bandpass solution — ✅ implemented
> *"To improve the signal-to-noise ratio (S/N), the bandpass was smoothed using a 9-channel
> box-car filter. Any gaps in the bandpass ... were interpolated."*

New `bookkeeping.smooth_bandpass_caltable(caltable, kernel)`: opens the bandpass caltable via
`casatools.table`, per antenna/pol row linearly interpolates over any still-flagged channels
(phase unwrapped first to avoid wrap-around artifacts at gap edges — this is *in addition to*,
not a replacement for, `bandpass()`'s own `fillgaps=8`, which only covers gaps up to 8 channels),
then box-car smooths. Wired into `xx_yy_solve.py` right after the `bandpass()` call, gated by new
`[crosscal] bpsmooth_kernel` (channels, default `0` = off; set to `9` to match the paper). Any
channel successfully interpolated over is unflagged afterwards.

**Bug caught during verification, not just theoretical**: a naive `np.convolve(data, box,
mode='same')` implicitly zero-pads outside the array, which biased the smoothed amplitude toward
zero near the two band edges — confirmed with a synthetic test showing up to ~45% amplitude error
in the first/last few channels for a 9-channel kernel, vs. ~1-3% in the interior. Fixed by
normalizing each output channel by the actual count of in-bounds samples contributing to it
(`np.convolve(np.ones(nchan), box, mode='same')` as the divisor) rather than the fixed kernel
size everywhere — re-verified: edge error dropped to ~1-2%, matching the interior, and overall
smoothed error is correctly below the pre-smoothing noise level. This would have silently
corrupted every bandpass solution's edge channels had it shipped as originally written.

### 2.3 Radio velocity convention instead of optical — declined
Considered switching `image_engine.run_stage()`'s `veltype='optical'`→`'radio'` (MHONGOOSE's own
choice, and arguably the field's more common default for HI). **Decision: staying with optical** —
radio convention judged outdated for this pipeline's purposes. No change made.

### 2.4 Two-stage iterative flagging (flag → calibrate → re-flag → re-calibrate)
The paper explicitly does two flag/calibrate cycles for both the primary-calibrator solve and the
secondary-calibrator gain tracking. Our `flag_round_1.py`/`flag_round_2.py` split already does
something similar in spirit (flag, calibrate, flag again, calibrate again) — worth explicitly
checking our existing two-round structure actually re-derives the *same* solutions the second time
(as MHONGOOSE does) rather than just adding more flags without a genuine re-solve. **Effort: review
only, likely already adequate — lowest priority of this list since it may already be covered.**

## 3. New tooling to consider (adds a dependency or meaningfully new code)

These are real, specific ideas from the paper/CARACal, ranked roughly by expected value — but each
needs a new container, a new library, or substantially more code than section 2, so they belong on
a separate track rather than blocking on immediate adoption.

- **AOFlagger** (new container/binary) — CARACal's primary RFI-flagging engine, generally regarded
  as more sensitive than CASA `tfcrop` for MeerKAT-scale RFI. Would need its own `ContainerProfile`
  entry (same pattern as `SOFIA_CONTAINER`) and `.lua`/`.rfis` strategy files tuned for our data.
  Biggest single flagging-quality lever available, but a real integration project, not a config
  tweak.
- **u=0 / horizontal-stripe flagging** (§5.4 of the paper — their own explicitly-claimed novel
  contribution): per-scan FFT-amplitude imaging of line-free channels, MAD-based iterative
  threshold search (`M`×MAD for `M`∈{100,150,200,300,500}, pick the `M` giving lowest noise), flag
  a small region around u=0/v=0 in the *uv*-plane. No new external package needed (numpy +
  `casatools.image`/`ms` suffice), but it's a genuinely new algorithm, not a parameter change —
  worth prototyping against a known-affected dataset before committing to it. CARACal's own
  implementation lives at `caracal/workers/utils/flag_Uzeros.py` if a reference implementation is
  wanted.
- **Cascading cross-resolution SoFiA mask chain**: build the lowest-resolution cube/mask first,
  regrid its final mask as the seed for the next resolution's deconvolution, repeat. This is a
  genuine architectural extension of our `[hi_image] hi_combos` mechanism (which already runs each
  robust/taper combination as a fully independent chain) — the new part is deliberately chaining
  combos in resolution order and passing a regridded mask between them instead of starting each
  from scratch. Meaningful implementation work in `hi_image.py`/`image_stages.py`, not just config.
- **WSClean** as an alternative deconvolver to `tclean` — a bigger, riskier swap (new container,
  different parameter surface, different multi-scale/w-projection behaviour) than anything else on
  this list. Only worth it if `tclean`'s performance/quality is a demonstrated bottleneck; not
  recommended as a near-term priority given how much of this refactor is already built around CASA
  tasks specifically.
- **breizorro** — CARACal's third possible masking backend (unconfirmed from what was read this
  session — worth checking CARACal's `mask_worker.py`/`mask_schema.yml` directly if pursuing this,
  since it wasn't independently verified here). Simpler/faster mask-from-image tool than PyBDSF/SoFiA
  for some use cases.
- **Noise-vs-integration-time QA check**: MHONGOOSE validates their sensitivity model by fitting the
  noise-vs-time slope (theirs: −0.504 vs. theoretical −0.5) and comparing measured vs. assumed
  system temperature. A nice-to-have standalone QA script (numpy + astropy, no new dependency), low
  priority relative to the flagging/contsub items above.

## 4. Where we're already ahead / already aligned

- **`[hi_image] hi_combos`** already generalizes MHONGOOSE's six hard-coded "standard resolutions"
  into a configurable list of `{robust, uvtaper}` combinations, each getting the full independent
  stage chain — more flexible than their fixed six.
- **Galactic HI exclusion** (`1419.8~1421.3MHz`) — added this session, and turns out to be the
  *exact* window the paper uses, for the same reason (foreground contamination for extragalactic
  targets).
- **SoFiA-driven masking for the final loop** — same tool CARACal/MHONGOOSE standardize on, now that
  this session's `mask_image()` fix stops PyBDSF from silently overwriting it.
- **Two-pass continuum subtraction, already in place** (an earlier version of this doc incorrectly
  claimed we were missing the first pass — corrected): `uvsub.py` (predates this refactor) subtracts
  the final self-cal loop's sky model — populated into `MODEL` by `selfcal_part2.py`'s own
  `tclean(niter=0, savemodel='modelcolumn', restart=True)` predict call — via CASA's native
  `uvsub()`, immediately before `uvcontsub.py`'s residual polynomial-fit pass. Functionally the same
  two-stage scheme as CARACal's Crystalball-based approach, built entirely from CASA's own tasks —
  no extra package needed on our side where CARACal needs a dedicated one.
- Both pipelines are container-based and config-file-driven at the same conceptual level (one YAML
  vs. one flat `myconfig.txt`) — the architectural gap is smaller than it might first appear; the
  concrete differences are almost entirely in *which tools* get wrapped (AOFlagger/WSClean vs.
  CASA `flagdata`/`tclean`), not in the overall pipeline shape.
