# Pawsey Pipeline Refactor Plan

## Status (for a fresh agent session picking this up)

Branch: `pawsey-refactor`, forked from `HI-pawsey`'s tip. Commits so far, oldest first:

```
ae770e4 Phase 0: branch setup for the Pawsey architecture refactor
19cbc77 Phase 1: add script_registry.py (declared per-script properties)
9b0f214 Phase 1 (1/5): migrate write_command()'s arrayJob check to script_registry
1ee6324 Phase 1 (2a/5): migrate write_sbatch()'s cpu-intensive heuristic to script_registry
2c762c8 Phase 1 (2b/5): migrate write_sbatch()'s polarisation-count override to script_registry
64b507c Phase 1 (2c/5): migrate write_sbatch()'s --exclusive branch to script_registry
06dca7b Phase 1 (2d/5): migrate write_sbatch()'s plot/casa-invocation branch to script_registry
a5b3d28 Phase 1 (2b-array): migrate write_sbatch()'s --array directive to script_registry
ea230a6 Phase 1 (2e/5): migrate write_sbatch()'s long-partition override to script_registry
ed18bb7 Phase 1 (3/5): migrate write_master()/write_spw_master() to script_registry
fd1db51 Move the refactor plan into the repo for cross-session handoff
a062602 Fix selfcal_part1 crash: clean up stale per-loop products before retrying (cherry-picked from HI-pawsey 543363b)
204353e Update REFACTOR_PLAN.md: HI-pawsey selfcal_part1 crash is now resolved
567fab7 Make golden_diff.sh robust to the checkout's absolute path
0e3c9ef Phase 1 (4a/5): migrate format_args()'s selfcal-present check to script_registry
5b818bd Phase 1 (4b/5): migrate format_args()'s calc_refant.py dedup check to script_registry
4b17741 Phase 1 (4c/5): migrate format_args()'s split.py threadsafety override to script_registry
fe57425 Phase 1 (4d/5): migrate format_args()'s dopol-forcing check to script_registry
ca9e44f Phase 1 (4e/5): migrate format_args()'s includes_partition check to script_registry
9f026f7 Phase 1 (5/5): migrate default_config()'s remove_scripts hack to script_registry
```

(4a–5 above were landed by an unattended cloud agent session; it hit its account's usage-session
limit partway through leaving 5/5 uncommitted in its worktree, which was then verified, committed, and
pushed directly rather than re-launching a new agent for one small piece.)

**Done: all of Phase 1.** The script-property registry (`processMeerKAT/script_registry.py`) exists and
every substring-matching-on-script-filename call site identified at the start of Phase 1 — in
`write_command()`, `write_sbatch()`, `write_master()`/`write_spw_master()`, `format_args()`, and
`default_config()` — is now migrated to read from it instead. Every commit verified against
`tools/golden_diff.sh` (run it — `./tools/golden_diff.sh` — before and after any change to
`write_command`/`write_sbatch`/`write_master`/`write_spw_master`/`format_args`; no diff means no
regression); `default_config()`'s migration (5/5) isn't reachable by that harness (`-B` needs a real MS,
which the harness deliberately doesn't exercise) and was instead verified with a one-off snippet comparing
old vs. new logic across all four `do2GC`/`science_image` combinations. Several other commits similarly
needed a manual spot-check beyond the harness where the fixture config
(`tools/golden_diff/fixture_config.txt`, `nspw=1`) doesn't exercise a branch (e.g. `nspw>1`, `dopol=True`,
`keepmms=False`) — see individual commit messages for exactly what was checked and how.

`567fab7` is a fix to the harness itself, not the pipeline: `tools/golden_diff.sh` was comparing generated
`.sbatch` output byte-for-byte including the absolute checkout path embedded in it (`PYTHONPATH`, the
script's own path), so running it from a different checkout location (e.g. a worktree-isolated agent
session under `.claude/worktrees/<id>/`) produced spurious diffs with zero actual code change. Now
normalizes the absolute path immediately preceding `/processMeerKAT` to a fixed placeholder before
comparing, on both sides. Worth knowing about if you ever see the harness disagree with itself between two
checkouts of the identical commit.

**Next step**: Phase 2 (the self-calibration stage-list restructuring) is next in sequence — see its full
write-up below. There's a stray git worktree at `.claude/worktrees/agent-a52e6fc8f4994bd7e/` (branch
`pawsey-refactor`, currently in sync with `origin/pawsey-refactor`) left over from the cloud agent session
above; safe to `git worktree remove` once you've confirmed nothing else needs it, or reuse it if resuming
that same agent.

**`HI-pawsey`'s `selfcal_part1` crash is resolved** (as of `HI-pawsey` commit `543363b`, cherry-picked here
as `a062602`). Phase 2's write-up below still contains a "Correction (2026-08-22...)" callout describing an
intermediate state where the first fix attempt (`0013ebe`, forcing `calcpsf=True`) turned out *not* to fix
the crash — that callout is now superseded by the real root cause and fix described right after it
(leftover stale `imagename.psf`/`.sumwt` files from a previous crashed attempt confusing `tclean`'s
`restart=True` path regardless of `calcpsf`; fixed by deleting `imagename.*` before every `tclean` call in
`selfcal_part1.py`). The full 4-stage HI loop has since been run successfully end-to-end on `HI-pawsey`
through loop 2 (`selfcal_part1`/`selfcal_part2` for both loops 1 and 2); loop 3 (the final deep clean,
`niter=1000000`) is next and is the one to watch for Phase 7b's 24h-walltime-cap concern, since unlike
loops 1–2 it isn't expected to stop early on threshold. Phase 2's stage-list restructuring is unaffected
either way (it was always a readability win independent of the bug); Phase 7b's checkpoint-chaining design
should still apply the same "always clean up, never assume leftover state is safe to reuse" lesson when
it's implemented, even though the specific bug that taught it is now fixed.

`HI-pawsey` has been pushed to `origin/HI-pawsey` through `543363b` (includes `2077eae` CLAUDE.md, `0013ebe`
the calcpsf change, and `543363b` the actual fix) — check `git log origin/HI-pawsey..HI-pawsey` if picking
this up later, in case more has landed since.

---

## Context

The IDIA MeerKAT pipeline (`processMeerKAT.py` and friends) was built for the Ilifu cluster and is
being ported to Pawsey's Setonix this session, entirely on the `HI-pawsey` branch via a long series of
targeted, hard-won fixes (container/MPI environment issues, Setonix's shared-node memory model, an
exclusive-job core-topology requirement, a `parallel=True`+`calcpsf=False` MPI major-cycle bug, etc. —
all documented in `CLAUDE.md` at the repo root). That branch is deliberately staying in "get it working
as-is" mode — a real end-to-end test run is in progress there right now.

This plan is for a **separate, new branch** that properly re-architects the pipeline for Pawsey rather
than patching around Ilifu-era assumptions in place, folds in the container recipe (currently living
outside git, at `/software/projects/pawsey1164/ssankar/containers/spack-idia-fixed.def`) as part of the
repo, and adds a genuinely new capability (config-driven HI cube imaging) that only exists today as a
hardcoded, disconnected prototype (`m2-image-scripts/` on `HI-dev`). `HI-pawsey`'s fixes get forward-ported
into this branch continuously as they land, not as a one-time step at the end — the two branches are
touching the same functions (`write_sbatch`/`write_command`) and will only get harder to reconcile the
longer they diverge.

Intended outcome: a new branch that eventually supersedes `HI-pawsey`, with cluster facts and workflow
choices exposed as config rather than buried in code, the substring-matching-on-script-name pattern
replaced by a declared script-property registry, the self-calibration loop's own implicit indexing
replaced by an explicit per-loop stage list, uvsub/uvcontsub decoupled from science imaging and
modernized onto CASA's new `uvcontsub` task, a real config-driven HI (cube) imaging pipeline, and a
parallelism strategy that respects Setonix's 24h `work`-partition walltime cap.

## Foundational decisions

- **Branch base**: fork off `HI-pawsey`'s current tip — the only branch with the Pawsey fixes already
  validated. Bring `containers/spack-idia-fixed.def` into the repo (new top-level `containers/`
  directory) in the first commit.
- **Continuous forward-porting**: after every `HI-pawsey` fix commit, cherry-pick it onto this branch
  promptly rather than batching all porting for the end.
- **Regression safety without a test suite**: the repo has no automated tests. For every change touching
  `write_command`/`write_sbatch`/`write_master`/`write_spw_master`/`format_args`, run `-B`+`-R` against a
  fixed test config before and after, and diff the generated `.sbatch`/master-script output byte-for-byte.
  Any unintended difference is a regression signal — cheap, and the closest thing to a test harness this
  codebase can get without a real MS.

## Phase 0 — Branch setup

Create the branch off `HI-pawsey`. Commit `containers/spack-idia-fixed.def` into a new `containers/`
directory. Set up the golden-diff harness (a small script that runs `-B`+`-R` against a fixed config and
diffs output against a saved baseline). No functional changes yet.

## Phase 1 — Declared script-property registry (foundation for goals 1, 2, 3, 5, 6)

Replaces the substring-matching pattern found throughout `processMeerKAT.py`
(`write_command()` line ~467, `write_sbatch()` lines ~573–651, `write_master()`/`write_spw_master()`'s
near-duplicated `nloops`-replication blocks around lines ~782–793/~897–914, `format_args()` lines
~1335/~1404–1430, `default_config()`'s `remove_scripts` list at lines ~1184–1200) — each of these
currently re-derives an undeclared "property" of a script (needs many cpus, needs the whole node, is the
SPW-fanout step, is a plotting script, requires an MMS, forces dopol, etc.) by string-matching the script's
filename, sometimes in 3+ separate places for the same property.

New module `processMeerKAT/script_registry.py`:
```python
@dataclass
class ScriptProperties:
    cpu_intensive: bool = False
    exclusive_node: bool = False
    is_spw_fanout: bool = False
    array_job_capable: bool = False
    plot: bool = False
    casa_invocation: bool = True
    long_running: bool = False
    requires_mms: bool = False
    forces_dopol: bool = False
    pipeline_role: str | None = None   # 'partition', 'selfcal_part1', 'science_image', 'hi_image', 'contsub', ...

REGISTRY: dict[str, ScriptProperties] = { ... }  # one entry per script in SCRIPTS/PRECAL_SCRIPTS/POSTCAL_SCRIPTS
```

Migrate call sites one small group at a time, each its own commit, each verified against the golden-diff
harness:
1. `write_command()`'s `arrayJob` check. **Done** (`9b0f214`).
2. `write_sbatch()`'s cpu heuristic, polarization-count override, `--exclusive` branch, plot/casa-invocation
   branch, `long`-partition override — split into sub-commits per heuristic given this is the most tangled
   function in the codebase. **Done** (`1ee6324`, `2c762c8`, `64b507c`, `06dca7b`, `a5b3d28`, `ea230a6`).
3. `write_master()`/`write_spw_master()`'s script-name checks — replace with `pipeline_role` lookups and
   de-duplicate the two near-identical `nloops`-replication blocks into one shared helper. **Done**
   (`ed18bb7`).
4. `format_args()`'s selfcal/threadsafety/dopol/dedup checks. **Done** (`0e3c9ef`, `5b818bd`,
   `4b17741`, `fe57425`, `ca9e44f` — split into 5 sub-commits, one per distinct check, following the same
   granularity as item 2).
5. `default_config()`'s `remove_scripts` hack. **Done** (`9f026f7`) — implemented as a `pipeline_role`-keyed
   filter (`remove_roles` set + list comprehension) rather than the dedicated `is_science_imaging`/
   `is_hi_imaging`/`is_contsub_step` boolean flags originally sketched here: `pipeline_role` already
   distinguishes `'selfcal_part1'`/`'selfcal_part2'`/`'science_image'` individually, so a set of roles to
   drop is simpler than three new single-purpose flags that would just re-encode the same three role names.
   Phase 4/5 (`-H` flag gating, uvsub/uvcontsub decoupling) should reuse this same `remove_roles`-style
   pattern rather than reintroducing the flags this superseded.

## Phase 2 — Self-calibration stage list: replace `loop`/`nloops` indexing (goal 5)

Motivated directly by a bug fixed on `HI-pawsey` this session (commit `0013ebe`): `selfcal_part1.py`'s
`symlink_psf()`-driven PSF-reuse optimization (`calcpsf=False` when re-using a previous loop's PSF/weight
density) broke `tclean`'s parallel MPI major cycle — the per-engine data selection needed to apply imaging
weights during the major cycle is only registered when the PSF is actually (re)computed, so skipping it
raised `RuntimeError: Imaging weight calculation is requested for a data that was not selected` partway
through an 11+ minute major cycle. The fix (always `calcpsf=True`) was a 5-line change, but finding it meant
untangling `selfcal_part1.py`/`selfcal_part2.py`/`bookkeeping.py`'s implicit per-loop bookkeeping — each
loop's role (dirty image vs. phase-selfcal vs. amp+phase-selfcal vs. final deep-clean) is reconstructed at
runtime from `nloops+1`-long parallel config arrays (`calmode`, `solint`, `niter`, `threshold`, `flag`,
`gridder`, `robust`, `imsize`, `cell`, `scales`, ...) indexed with an off-by-one convention
(`calmode[loop-1]` = the cal *derived from* the previous loop's image, `calmode[loop]` = the cal to
*derive after* this loop's image), plus `bookkeeping.get_selfcal_params()`'s broadcast machinery
(`single_args`/`gaincal_args`/`list_args`, the "`+1` is a Hacky fix to avoid indexing errors" comment,
lines 131–176). Nothing states in one place that "loop 1 exists to produce a phase-only calibration
solution" — that fact only exists as `calmode[1] == 'p'`.

**Correction (2026-08-22, after re-running on `HI-pawsey`)**: the `calcpsf=True` fix above by itself does
**not** resolve the crash. Re-running loop 1 with just that fix applied hit the identical
`RuntimeError: Imaging weight calculation is requested for a data that was not selected`, on the same
MPI server rank (1), but now raised from `makepsf()` itself rather than `executemajorcycle()`. Investigated
the mask directly at this point (opened `im_0.islmask`/`im_0.pixmask` with `casatools.image`, compared
coordinate systems and pixel content against `im_0.image` and the `im_1` definition from the tclean log) —
the mask is correctly formed and not the cause; the `usemask='user'` interaction was a reasonable suspect
but turned out to be a red herring. **Actual root cause, found and fixed (`HI-pawsey` commit `543363b`,
here as `a062602`)**: a stale `imagename.psf`/`.sumwt` symlink pair, left over on disk from the *earlier*
crashed run of the old `calcpsf=False` PSF-reuse code, was never cleaned up before the next attempt.
`tclean`'s `restart=True` path found that pre-existing `.psf`/`.sumwt` (pointing at a *different* image's
already-finalized weights) and, despite `calcpsf=True` being passed, ended up with inconsistent per-engine
PSF/weight registration — reproducing the identical error regardless of the `calcpsf` value. Fix: delete
any `imagename.*` leftovers before every `tclean` call in `selfcal_part1.py`, since `calcpsf` is always
`True` now and there's never a legitimate reason to reuse a previous attempt's partial products. **Verified
end-to-end**: loop 1's `selfcal_part1`+`selfcal_part2` both completed successfully with this fix (loop
advanced to 2, `.gcal1` phase caltable produced, new `im_1.pixmask` generated for loop 2). Phase 7b's
checkpoint-chaining design (below) should still carry forward the general lesson — never assume leftover
on-disk state from a previous attempt is safe to reuse, clean up explicitly — even though the specific bug
that taught it is now fixed.

**Confirmed canonical HI workflow** (from the user, and already what the default config's arrays encode):
loop 0 = dirty image, used only to derive an initial clean mask (no calibration); loop 1 = image using
loop 0's mask, then derive a phase-only solution; loop 2 = apply the phase solution, image using loop 1's
mask, then derive an amp+phase solution; loop 3 (`== nloops`) = apply the amp+phase solution, image using
loop 2's mask (deep clean), producing the final model/mask used for contsub and science imaging. This
4-stage recipe should ship as the HI workflow's default — but users must still be able to lengthen the
self-cal chain without hand-editing eight parallel arrays in lockstep.

**Design — replace N parallel per-loop arrays with one list of per-loop stage records.**

New `[selfcal] stages` config value: an ordered list of small dicts, one per loop (including loop 0), each
fully self-describing:
```python
stages = [
    {'mask': None,   'apply_cal': None,   'derive_cal': '',   'niter': 10000,   'threshold': '0.5mJy'},
    {'mask': 'prev', 'apply_cal': None,   'derive_cal': 'p',  'niter': 50000,   'threshold': 10,  'solint': '1min'},
    {'mask': 'prev', 'apply_cal': 'prev', 'derive_cal': 'ap', 'niter': 80000,   'threshold': 5,   'solint': '10min'},
    {'mask': 'prev', 'apply_cal': 'prev', 'derive_cal': '',   'niter': 1000000, 'threshold': 3},
]
```
- `nloops` is derived (`len(stages) - 1`), not separately configured — removes the entire class of
  "array length must match nloops+1" validation errors currently raised in `get_selfcal_params()`.
- Extending the chain = appending one dict to the list. No re-indexing of seven other arrays, no
  `loop-1` arithmetic to get right.
- `mask`/`apply_cal` are relative references (`None` or `'prev'`) rather than being recomputed from
  `loop-1`/`loop` arithmetic scattered across `bookkeeping.get_selfcal_args()` (lines 241–245) — the
  dependency between consecutive stages becomes explicit and local to each stage's own entry, instead of
  implied by its position in a shared array.
- Keys that genuinely don't vary by stage (`gridder`, `wprojplanes`, `uvrange`, `scales`, `gaintype`)
  stay as single top-level `[selfcal]` scalars, unchanged; only the values that actually differ per loop
  move into the stage dicts.
- `default_config.txt`'s HI template ships the 4-entry list above as the out-of-the-box default. A user
  who wants a different loop count edits this one list instead of eight.
- Delete `bookkeeping.get_selfcal_params()`'s `single_args`/`gaincal_args`/`list_args`/broadcast-to-
  `nloops+1` machinery (lines 131–176) outright — a `Stage` dataclass (with sensible field defaults)
  replaces it. `get_selfcal_args()`'s `loop-1`/`loop` indexing (lines 233, 241–245, 263–268) becomes direct
  lookups into `stages[loop]` / `stages[loop-1]`.
- `selfcal_part1.py`/`selfcal_part2.py` iterate the resolved `Stage` object for their parameters instead
  of `selfcal_part1()`'s current ~20-positional-argument signature built from same-length lists — shrinks
  that signature substantially.
- **Carries the bugfix's lesson forward deliberately**: this restructuring does not reintroduce PSF-reuse.
  Phase 7b (below) still needs a warm-start mechanism for long-running loops, but it defaults to always
  recomputing the PSF (per this session's finding) and only skips it as an explicit, separately-justified,
  separately-tested opt-in — never as an implicit "looks safe" heuristic buried in a boolean condition the
  way `symlink_psf`'s call site was.

Sequenced right after Phase 1 (it's the same "declare properties instead of re-deriving them" pattern,
applied at the self-cal-loop level instead of the script level) and before Phase 6/7 (both the
m2-image-scripts port and the 24h-walltime checkpoint-chaining extend per-loop `tclean` behavior, and
should be built against the new stage record rather than the old array indexing).

## Phase 3 — Cluster-hardware config into `myconfig.txt` (goal 1)

New `[cluster]` section in `default_config.txt` + a `CLUSTER_CONFIG_KEYS` list (mirroring
`SLURM_CONFIG_KEYS`) holding: `total_nodes_limit`, `cpus_per_node`, `mem_per_node_gb`,
`mem_per_node_gb_highmem`, `mem_per_cpu_mb_shared`, `default_mem_gb`, plus named partition keys
(`default_partition`/`long_partition`/`highmem_partition`/`devel_partition`, replacing the inline
`'work'`/`'long'`/`'HighMem'`/`'Devel'` string literals scattered through `write_sbatch()`) and a
Pawsey-appropriate `account` default (replacing the stale Ilifu `'b03-idia-ag'` in `write_jobs()`).

As an **isolated commit**, separate from the mechanical config move: verify Setonix's real physical core
count and fix `CPUS_PER_NODE_LIMIT` if it's stale (this session's own debugging found the nodes have 256
logical CPUs / 128 physical cores via `ThreadsPerCore=2`, while the current constant is `64` — worth
confirming precisely and fixing here, kept separate so a wrong assumption doesn't get buried in a large
mechanical commit).

Restructure `CONTAINER_PYTHON`/`CONTAINER_ENV`/`CONTAINER_BINDS`/`CONTAINER_PREPEND_ENV` into one
`ContainerProfile` registry — **stays code, not user config**. These are deep technical workarounds tied to
one specific container build (documented in `CLAUDE.md`); if `[slurm] container` becomes freely swappable
these silently stop applying to any other container. Add a `logger.warning` when a configured container
isn't in the registry and isn't the known default, so a user swapping containers gets an explicit
heads-up instead of silently losing every Pawsey-specific fix.

## Phase 4 — Continuum-vs-HI workflow toggle (goal 2) — confirmed: independent flags

Add `-H/--hi_image` alongside the existing `-I/--science_image`, same pattern, both able to run together
(continuum self-cal imaging + separate HI cube imaging in one pass — matches goal 7's "additive" framing).
`nspw` remains purely the SPW-splitting count, untouched by this flag. `default_config()`'s `remove_scripts`
logic (now driven by Phase 1's `is_hi_imaging`/`is_contsub_step` flags) gates the new Phase 6 scripts on
`-H`. Revert `[image] specmode` default in this branch's `default_config.txt` template to `'mfs'` (true
continuum) now that HI cube imaging gets its own dedicated pipeline in Phase 6 — template-only change,
doesn't touch `HI-pawsey`'s in-flight config.

## Phase 5 — Decouple uvsub/uvcontsub, modernize to the new `uvcontsub` task (goals 3, 4)

**Root issue behind goal 3**: `uvcontsub.py` currently overwrites the shared `[data] vis` key, which
`science_image.py` transparently (and silently) consumes next in the `POSTCAL_SCRIPTS` list — an implicit
handoff through mutable shared config state, not an explicit one. Confirmed bug: `uvsub.py`/`uvcontsub.py`
are never gated by `-I` today — they run unconditionally whenever `nspw > 1`.

- Gate `uvsub.py`/`uvcontsub.py` on the new `-H` flag, with an independent `--contsub` override for users
  who want contsub'd visibilities without full cube imaging.
- **Stop `uvcontsub.py` from overwriting `[data] vis`.** Write to a new, explicitly-named
  `[run] hi_contsub_vis` key instead, with a `bookkeeping.get_hi_contsub_vis()` accessor that only Phase 6's
  HI scripts call explicitly. `science_image.py` (continuum) no longer implicitly depends on contsub having
  run or on script-list ordering.
- **Preserve a standalone continuum-only MS** (confirmed needed — do not drop `want_cont`'s equivalent):
  the new `uvcontsub` task has no direct `want_cont` parameter, so reproduce it explicitly: call
  `uvcontsub(..., writemodel=True)`, then a follow-up `split(vis=vis, outputvis=vis+'.cont',
  datacolumn='model')` to materialize the continuum-only MS. Write its path to a new
  `[run] continuum_vis` key (replacing today's write-only, never-read version of that key) with a real
  accessor so downstream consumers can actually use it.
- Migrate the core call (today's commented-out attempt at `uvcontsub.py:17` gets finished, not
  re-invented):
  ```python
  uvcontsub(vis=vis, outputvis=outputvis, datacolumn='corrected', fitspec=fitspw, fitorder=fitorder, writemodel=True)
  ```
  `fitspec` takes the same MSSelection string `fitspw` already uses (direct rename, no semantic change).
  `combine='',solint='int'` (today's hardcoded finest-granularity, i.e. effectively no time averaging) has
  no equivalent in the new task, but dropping it should reproduce equivalent behavior since the old
  settings already disabled averaging.
- Handle the new task's "errors if `outputvis` already exists" behavior with a skip-if-exists guard,
  matching `science_image.py`'s existing `if not os.path.exists(...):` idempotency convention.

## Phase 6 — Port `m2-image-scripts/` into the config-driven pattern (goal 7)

Sequenced after Phase 1 (registry), Phase 4 (`-H` flag) and Phase 5 (`hi_contsub_vis` handoff).
Reference implementation: `m2-image-scripts/` already exists as a working-but-hardcoded prototype on
`HI-dev` (`combine_tracks.py`, `m2h0/hicube0.py` + SoFiA pass, `m2h1/hicube1.py` + SoFiA pass,
`fincubes/` rebin + SoFiA pass) — CASA5/mpicasa inline-script style, fully hardcoded paths, not using
`config_parser`/`bookkeeping`/the script-list machinery at all. Port the *parameter choices*
(tclean/SoFiA settings), not the code structure.

- **6a**: `hicube0.py` (dirty cube `tclean` + `exportfits` + `immoments`) + its SoFiA mask pass, rewritten
  as `casatasks` function calls reading a new `[hi_image]` config section using the paired-list convention
  already established by `[selfcal]` (e.g. `hi_niter=[50000,1500000]`, `hi_threshold=['0.6mJy','0.24mJy']`,
  plus `scales`/`gridder`/`wprojplanes`/`deconvolver`/`weighting`/`robust` lifted from the prototype's
  literal values as defaults). Reuse `restfreq`/`imspw` from the existing `[image]` section rather than
  duplicating them. SoFiA `.file` templates adapted from the pattern already established by
  `aux_scripts/run_sofia.py` (note: that's a *different* SoFiA usage — continuum subtraction masking — from
  this one; don't conflate them when refactoring).
- **6b**: `hicube1.py` (import SoFiA mask, final deep clean with `pbcor`, moments/rebin/fincubes export),
  same config-driven treatment. Preserve the prototype's `tclean`-internal PB correction (`vp.setpbnumeric`/
  `vptable`) as-is rather than unifying with `science_image.py`'s post-hoc katbeam-based `do_pb_corr` — a
  radio-astronomy correctness call (per-channel PB variation across a cube may need this specific approach),
  not something to silently merge during a refactor.
- **6c**: `combine_tracks.py` — confirmed this combines two *independently run* pipeline passes (separate
  observing tracks, each with their own full calibration run), not two things within one run. Build as a
  small standalone multi-run orchestration tool (e.g. `combine_tracks.py <config1> <config2> <output>`,
  taking two runs' output MS paths/configs as arguments) rather than forcing it into the `SCRIPTS`-tuple
  single-run model where it doesn't fit.
- New entries added to `POSTCAL_SCRIPTS`/`postcal_scripts` for 6a/6b (threadsafe/container tuples, the
  three SoFiA passes reusing the already-registered SoFiA container), gated behind `-H` via Phase 4's
  registry-driven `remove_scripts`.

## Phase 7 — Parallelism strategy for Setonix's 24h cap (goal 6) — confirmed: split into two tracks

- **7a (Memo-13 array jobs, for Phase 6's genuinely cube-mode HI imaging)**: an opt-in `channel_parallel`
  toggle in `[hi_image]`. Extend Phase 1's registry with `array_job_capable` so the cube-imaging scripts
  reuse the *existing* SLURM `--array` machinery (`write_command()`'s array-job branch, `nconcurrent`
  CPU-budget limiting, `write_spw_master()`'s dependency chaining) instead of inventing a parallel bespoke
  mechanism. Each array task runs an ordinary non-MPI, single-channel (or `channels_per_job`-chunked)
  `tclean(specmode='mfs', spw=<single channel>, parallel=False)`; a concat step using CASA's virtual concat
  (`image.imageconcat()`, ~10s for 1000 channels per Memo 13) runs once the array completes. Sequence after
  a working non-parallel Phase 6 baseline exists, so the stitched output can be validated against a
  known-good single-job reference cube.
- **7b (checkpoint-and-chain, for `selfcal_part1`'s actual 24h-cap risk)**: `selfcal_part1` is `mfs`
  (continuum, no channel axis — Memo 13's technique doesn't apply to it directly, confirmed by reading its
  actual `tclean` call). Instead: split one selfcal stage's `niter` budget across multiple sequential sbatch
  jobs chained with the existing `-d afterany` dependency pattern, warm-started via `restart=True` on the
  same imagename across jobs (`calcres=False` on resume, to avoid recomputing an already-current residual).
  **Caution carried over from the Phase 2 bugfix** (now root-caused and fixed, see the Phase 2 correction
  above): the actual bug wasn't `calcpsf=False` per se -- it was `tclean`'s `restart=True`+`parallel=True`
  path getting inconsistent per-engine PSF/weight registration whenever it found a pre-existing
  `.psf`/`.sumwt` at the target imagename path that wasn't genuinely fresh for *this* run, whether that came
  from an intentional cross-image symlink (the original `symlink_psf()` design) or stale leftover state from
  a previous crashed attempt (what actually bit us). The general lesson still applies here: same-imagename
  resume across chained jobs is exactly this scenario again (a deliberately pre-existing `.psf`/`.sumwt` at
  the target path from the *previous* job in the chain) -- don't assume it's exempt just because it's a
  same-imagename continuation rather than a symlink. Validate a minimal `calcpsf=False, calcres=False,
  restart=True` resume of the same imagename under `parallel=True` in isolation (small MS, cheap stage)
  *before* building the full checkpoint-chain on top of it. If it hits the identical MPI registration
  failure, default every resumed
  job to `calcpsf=True` as well (cheap relative to a whole major cycle — the same trade the Phase 2 bugfix
  made) rather than relying on `calcres=False`-only resume. `tclean` already stops early on threshold
  regardless of `niter` budget, so chaining itself is safe either way. No new SLURM-array infrastructure
  needed — directly targets the stated problem.

## Phase 8 — Ongoing `HI-pawsey` fix porting & cutover

Not a one-time step — a recurring practice throughout Phases 1–7 (see Foundational Decisions). Once this
branch's baseline (through at least Phase 5) is validated via golden-diff and ideally a real small-MS run,
it can start superseding `HI-pawsey`.

## Verification

- No automated test suite exists; validate via the golden-diff harness (Foundational Decisions) after every
  commit touching job-generation code, and via an actual `-B`/`-R`/`./submit_pipeline.sh` run against the
  same small test MS used on `HI-pawsey` this session (`/scratch/pawsey1164/ssankar/pipe_test/1738276790.ms`)
  once Phase 5 lands, to confirm the decoupled uvsub/uvcontsub + continuum imaging path produces equivalent
  output to `HI-pawsey`'s current run.
- Phase 6/7a's HI cube imaging needs its own validation once ported: compare a Memo-13 array-job stitched
  cube against a single-job reference cube for numerical consistency (matching Memo 13's own validation
  approach — flux density/RMS/beam comparison per channel).
- Phase 2's stage-list restructuring should be validated by running the full HI 4-stage default against the
  same test MS and confirming it reaches loop 3 (`_im_3`) without the `calcpsf`-related crash the original
  `HI-pawsey` run hit, plus a golden-diff-style check that generated per-loop `tclean` parameter dumps
  (visible in the CASA log, e.g. line 49's parameter dump style) match the equivalent hand-computed values
  from the old array-indexed code, so the restructuring is confirmed behavior-preserving before Phase 7b
  builds on top of it.
