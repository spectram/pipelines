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
913d80c Phase 2 (1/4): add selfcal_stages.py (Stage dataclass + stage-list resolution)
a6c182a Phase 2 (2/4): cut [selfcal] config over to the 'stages' list
e7e7607 Phase 2 (3/4): selfcal_part1.py -- thin mechanical update for the stage list
55588c7 Phase 2 (4/4): selfcal_part2.py -- thin mechanical update for the stage list
8b92821 Stop forcing selfcal_part1/part2 onto the scarce 'long' partition
91d00b3 Write a listobs summary alongside the config during -B
0c8ab55 Phase 3: fix stale CPUS_PER_NODE_LIMIT (64 -> 128, Setonix's real core count)
0b4c9f1 Phase 3: move cluster hardware facts and named partitions into [cluster] config
bdd5f82 Phase 3: consolidate CONTAINER_* dicts into one ContainerProfile registry
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

There's a stray git worktree at `.claude/worktrees/agent-a52e6fc8f4994bd7e/` (branch `pawsey-refactor`,
currently in sync with `origin/pawsey-refactor`) left over from the cloud agent session above; safe to
`git worktree remove` once you've confirmed nothing else needs it, or reuse it if resuming that same agent.

**Done: Phase 2's stage-list restructuring is implemented and code-complete** (`913d80c`, `a6c182a`,
`e7e7607`, `55588c7`), following the design in the "Phase 2" section below with one clarification (see
"Correction (2026-08-23)" below). **It is NOT yet run on real CASA/Setonix and must not be trusted in
production until it is** — see the verification breakdown right after this paragraph for exactly what was
and wasn't checked, and why. This was done under a hard constraint: no Setonix/CASA/Singularity access this
session, and `selfcal_part1.py`/`selfcal_part2.py` `from casatasks import *` at module level, so those two
files could not even be *imported* here, let alone executed — verification of them was necessarily limited
to `ast.parse()` (syntax only) and manual review.

What shipped:
- New `processMeerKAT/selfcal_stages.py` (zero CASA imports, fully unit-testable with plain `python3`): a
  frozen `Stage` dataclass (`mask`/`apply_cal`/`derive_cal`/`niter`/`threshold`/`solint`) plus
  `parse_stages()` (validates a raw `[selfcal] stages` config value, including that stage 0 can't reference
  `'prev'`), `nloops()` (`len(stages)-1`), `resolve_mask()`, and `should_apply_prev_cal()`.
- `[selfcal]`'s `nloops`/`niter`/`threshold`/`calmode`/`solint` keys replaced by one `stages` list in both
  `default_config.txt` and `tools/golden_diff/fixture_config.txt`; `SELFCAL_CONFIG_KEYS` and
  `expand_selfcal_loop_scripts()` (nloops now derived from `len(stages)-1`) updated to match.
- `bookkeeping.get_selfcal_params()`: the `single_args`/`gaincal_args`/`list_args` broadcast-to-`nloops+1`
  machinery (previously lines 131–176) deleted outright; now parses `stages` via
  `selfcal_stages.parse_stages()` and leaves every other `[selfcal]` key as the plain scalar/list the user
  configured (no more implicit replication, no more length validation to get wrong).
- `bookkeeping.get_selfcal_args()`: takes `stages` instead of separate `nloops`/`calmode`/`threshold`;
  resolves pixmask via `selfcal_stages.resolve_mask()` instead of the loop-1/loop calmode-inferred blanking
  condition; the "missing caltable" check reads `stages[i].derive_cal` instead of `calmode[i]`.
- `selfcal_part1.py`/`selfcal_part2.py`: thin, mechanical changes only, per the task's design — every
  `<param>[loop]` becomes the direct unindexed scalar (none of `imsize`/`cell`/`robust`/`wprojplanes`/
  `deconvolver`/`gridder`/`nterms`/`scales`/`gaintype`/`uvrange` actually varied per loop in any shipped
  config), `niter`/`threshold`/`calmode`/`solint` come from the resolved `Stage`, and the
  `calmode[loop-1] != ''` gate on applying the previous loop's cal becomes
  `selfcal_stages.should_apply_prev_cal()`. The `tclean()`/`gaincal()`/`applycal()`/`flagdata()` calls
  themselves, the `calcpsf=True` fix, and the pre-tclean stale-product cleanup are untouched.

**Correction (2026-08-23, while implementing)**: the Phase 2 write-up below doesn't explicitly say what
happens to keys that don't vary per loop in the shipped default (`imsize`, `cell`, `robust`, `wprojplanes`,
`deconvolver`, `nterms`, `gaintype`, `uvrange`, `flag`) beyond naming `gridder`/`wprojplanes`/`uvrange`/
`scales`/`gaintype` as examples that "stay as single top-level `[selfcal]` scalars, unchanged." Implemented
that literally for every key not present in the `stages` dict example (`mask`/`apply_cal`/`derive_cal`/
`niter`/`threshold`/`solint`): they're used directly as configured, with the old broadcast-to-`nloops+1`
machinery removed rather than kept for them. This does drop the pre-existing (if never actually exercised by
the shipped default) flexibility to give per-loop-varying `imsize`/`scales`/etc. via a full `nloops+1`-long
list of lists — anyone who actually wants that today would need to re-add it deliberately. Also: `flag`
(residual-flagging toggle after applying a previous cal) isn't in the `stages` dict example either, and the
shipped default is a single scalar `True`, so it stayed a top-level scalar too, applied whenever
`should_apply_prev_cal()` is true (this was already always `True` in every shipped config regardless of
loop, so behaviour is unchanged for the default; a config that varied `flag` per loop would need updating).

**Verification breakdown — read this before trusting any of Phase 2 in production**:
- **Fully unit-tested, no CASA needed** (`selfcal_stages.py` has zero CASA imports, and
  `bookkeeping.get_selfcal_params()` has none at module level either — only `get_selfcal_args()` does, via a
  local `from casatools import msmetadata,quanta`): `selfcal_stages.parse_stages()`/`nloops()`/
  `resolve_mask()`/`should_apply_prev_cal()` against the shipped 4-stage HI default (mask/apply_cal
  resolution at every loop), all six validation-error paths, and that extending the chain by appending a
  5th stage needs no other edits. Separately, `bookkeeping.get_selfcal_params()` was called directly (via
  `PYTHONPATH`, a temp config file, no CASA) against both a valid new-schema `[selfcal]` section (confirms
  `stages` resolves to the expected `Stage` list end-to-end through real config-file parsing, not a mock)
  and an invalid one (confirms it `sys.exit(1)`s with a logged error rather than crashing with a raw
  traceback).
- **Verified via `tools/golden_diff.sh`**: ran green before any Phase 2 change; after the config-schema
  cutover commit (`a6c182a`) it showed exactly one diff (the fixture config's own `[selfcal]` text, the
  intended schema change) with the *generated* sbatch/master scripts byte-identical — confirming
  `expand_selfcal_loop_scripts()`'s new `len(stages)-1` derivation reproduces the same 3-loop
  `selfcal_part1`/`selfcal_part2` replication as the old `nloops=3` key, and that `SELFCAL_CONFIG_KEYS`
  exactly matches the new fixture (no "unknown key"/"missing key" warnings in `-R`'s output). Baseline
  updated (`--update-baseline`) since the diff was intentional. Re-ran green (no diff) after the
  `selfcal_part1.py`/`selfcal_part2.py` commits too, as expected since neither file is ever imported by
  `processMeerKAT.py`'s job-generation path — this harness does not and cannot cover those two files at all.
- **NOT verified — needs a real Setonix/CASA run before trusting in production**:
  `bookkeeping.get_selfcal_args()` itself (the `from casatools import msmetadata,quanta` function body:
  pixmask/rmsfile/threshold resolution, the outlier-file/sky-model logic, the usermask-import branch) and
  everything in `selfcal_part1.py`/`selfcal_part2.py` (the actual `tclean()`/`gaincal()`/`applycal()`/
  `flagdata()`/PyBDSF calls, and whether the resolved `Stage` values reach them correctly at runtime). These
  got `ast.parse()` (syntax-check only) plus careful manual reading and cross-checking against the original
  logic, and three targeted programmatic checks that don't require CASA: (1) `selfcal_part1()`'s full
  parameter set exactly equals `SELFCAL_CONFIG_KEYS + {vis,refant,dopol}` (what
  `bookkeeping.get_selfcal_params()` actually produces) via an `ast`-based comparison script, not eyeballing;
  (2) the same for `selfcal_part2()`; (3) `find_outliers()`'s parameters minus `'step'` exactly equal
  `mask_image()`'s parameters minus `{outlier_base,outlier_image}` exactly equal that same expected set —
  load-bearing because `find_outliers()` does `local = locals(); local.pop('step')` then
  `mask_image(**local, ...)`, so a name mismatch there would silently break at runtime, not at import time.
  None of this substitutes for actually running `tclean`/`gaincal` against a real MS. **The next session with
  Setonix access should run the full 4-stage HI default against
  `/scratch/pawsey1164/ssankar/pipe_test/1738276790.ms` (per this doc's own "Verification" section) and
  confirm it reaches loop 3 (`_im_3`) exactly as `HI-pawsey`'s already-validated run did, before this branch's
  selfcal path is trusted over `HI-pawsey`'s.**

**Pre-existing bug found while reading `selfcal_part2.py`, not fixed (out of Phase 2's scope)**: `pybdsf()`
(a module-level function, not nested in `find_outliers()`) references a bare `loop` name that is not one of
its own parameters and is not a module global defined anywhere at import time. It only happens to resolve
today because this script is always invoked as `__main__` (`python3 selfcal_part2.py ...`), where the
`if __name__ == '__main__':` block's `loop = params['loop']` assignment lands in the same module-global
namespace `pybdsf()` reads from at call time. If `pybdsf()` were ever called from a context that imports
`selfcal_part2` as a module without that `__main__` block having run first (e.g. a future test, or a
different entry point), it would raise `NameError: name 'loop' is not defined`. Worth fixing whenever
`selfcal_part2.py` is next touched, but unrelated to the stage-list restructuring so left alone here.

**Next step**: Phase 2's code is done but unverified beyond what's listed above — get a real Setonix run in
before moving on, or at minimum flag this prominently to whoever does. After that, Phase 3 (cluster-hardware
config) is next in sequence per the write-up below.

**Done (2026-08-25): reduced-scale real-CASA smoke test of Phase 2 — passed.** As a cheap first step before
committing to the full production-scale 4-stage run this section calls for. Isolated test dir
`/scratch/pawsey1164/ssankar/pipe_test_refactor/` (a copy of `pipe_test/`'s already-split
`1738276790.1410~1420.0MHz.NGC4064.mms`, so `HI-pawsey`'s completed reference outputs in `pipe_test/` weren't
touched), config trimmed to just `selfcal_part1.py`/`selfcal_part2.py` (skips crosscal — reuses the
already-calibrated split MMS directly) against a **3-stage** `stages` list (dirty → phase-only selfcal →
apply-and-deepen; skips the amp+phase loop) at a much smaller `imsize=[800,800]`/`wprojplanes=64` and
correspondingly small `niter`/`threshold` than the validated production settings
(`imsize=[6144,6144]`/`wprojplanes=512`).

**First attempt (jobs 47572983–47572987) hit an unrelated infrastructure problem, not a Phase 2 bug**:
`selfcal_part1`/`selfcal_part2` were unconditionally forced onto Setonix's `long` partition (8 nodes, 4-day
cap) regardless of the actual job's resource needs, and the lead job's projected start time was ~24h out
purely from queue priority. Fixed at the code level (see the `long_partition` commit below) rather than
worked around per-job; re-submitted as jobs 47573195–47573199 on `work` (1368 nodes, 24h cap) and all 5
started within seconds/minutes and **completed successfully end-to-end** (`sacct`: all `COMPLETED`, exit
0:0, 00:01:52/00:00:28/00:01:47/00:04:24/00:02:17 elapsed). Verified from the actual CASA task logs
(`pipe_test_refactor/logs/*.casa`), not just exit codes:
- Loop 0: `tclean` dirty image produced `im_0.image`/`.psf`/`.pb`/etc.; `selfcal_part2`'s `pybdsf`+`mask_image`
  produced `im_0.pixmask`/`.islmask`/`.rms`.
- Loop 1 (`resolve_mask('prev')`): `tclean` correctly used loop 0's `.pixmask`; `selfcal_part2` then called
  `gaincal(caltable='...gcal1', solint='1min', calmode='p', ...)` — an exact match to `stages[1]`'s
  configured `solint`/`derive_cal` — and it solved cleanly (240/240 solution intervals succeeded).
- Loop 2 (`should_apply_prev_cal()` + `apply_cal='prev'`): `selfcal_part1` correctly called
  `applycal(gaintable=['...gcal1'], interp=['linear,linearflag'], ...)` followed by
  `flagdata(mode='rflag', datacolumn='RESIDUAL', ...)` (the `flag=True` residual-flagging path), then its
  `tclean` deep-ish clean completed, producing `im_2.image`/`.psf`.

So `parse_stages()`/`resolve_mask()`/`should_apply_prev_cal()`/`get_selfcal_args()` all round-tripped
correctly through real CASA, including the `mask='prev'`, `derive_cal` (with `solint`), and `apply_cal='prev'`
(with residual flagging) code paths. **Still not a substitute for the full production-scale validation run**
this section calls for — didn't confirm loop 3's `niter=1000000` deep clean behaves at production scale or
wall-time, doesn't reuse the real 4-stage default, and a much smaller image/wproject count could mask issues
that only show up at production scale (memory pressure, wproject plane count interactions, `long`-partition
walltime behavior since this test no longer even exercises that partition — see below). No CASA errors,
tracebacks, or "severe" log lines anywhere across the run.

**Found and fixed one genuine infrastructure bug while setting this up, unrelated to Phase 2 itself**:
`write_sbatch()` forced `selfcal_part1`/`selfcal_part2` (and `science_image.py`) onto `long` via the
`long_running` script-registry flag, conflating "needs the `ulimit -n` bump" with "needs a 4-day walltime
cap" — `long` has only 8 nodes vs. `work`'s 1368, so this caused severe, resource-need-independent queue
delays (confirmed: a job needing a fraction of a node projected to start ~24h out on `long`, same code
started within seconds on `work`). Fixed by splitting the partition-forcing behaviour into its own
`long_partition` registry property, decoupled from `long_running`'s ulimit-bump semantics; left unset
(defaults to `[slurm] partition`) for `selfcal_part1`/`selfcal_part2`, kept `True` for `science_image.py`
(unchanged). Verified via `golden_diff.sh` — only `selfcal_part1.sbatch`/`selfcal_part2.sbatch` changed
(`partition: long` → `work`), `science_image.sbatch` byte-identical. This trades away automatic protection
from loop 3's deep clean exceeding `work`'s 24h cap; per user direction, the intended mitigation is
increasing parallelism (`ntasks_per_node`) to fit the walltime rather than relying on `long` (which is
frequently unavailable in practice) — Phase 7b's checkpoint-chaining design is the real long-term answer for
genuinely walltime-risky loops. Also found (but did not fix, out of scope here):
`expand_selfcal_loop_scripts()` only appends the final loop's `selfcal_part2.sbatch` when `run_sofia.py`
immediately follows the selfcal pair in `[slurm] scripts` — a scripts list ending in bare
`selfcal_part1.py`/`selfcal_part2.py` silently drops the last loop's `part2` (no gaincal, no mask); confirmed
pre-existing behaviour via the golden-diff baseline, not a Phase 2 regression, but worth fixing or at least
documenting prominently if `run_sofia.py`-less scripts lists are ever a real configuration (this smoke test's
own config sidesteps it by making the interesting phase-cal loop not the last stage).

**Done (2026-08-25): cross-branch `-B` comparison against a real, new production MS — closes a
previously-flagged gap.** The user's real `HI-pawsey`-branch production run in
`/scratch/pawsey1164/ssankar/HI_p1/` (a fresh dataset,
`N4064_HI/1738276794/1738276794-sdp-l0_2025-07-14T18-45-05_mmu.ms`, `nspw=11` — separate from the
`pipe_test`/`pipe_test_refactor` MS used above, and **not otherwise touched or run further by this
session** per explicit instruction) gave a real opportunity to close a gap called out earlier in this
doc: Phase 1's `default_config()` migration (5/5, `remove_scripts` → `remove_roles`) was previously
verified only with a synthetic snippet, since `-B` needs a real MS and the golden-diff harness
deliberately doesn't exercise it. Ran `-B` against the same real MS from both branches (`HI-pawsey` via a
throwaway worktree, `pawsey-refactor` from this checkout — both need the container path to be visible
inside Singularity, so worktrees must live under `/software/projects/...` or `/scratch/...`, not `/tmp`;
also needs `module load singularity/4.1.0-mpi` and `-A pawsey1164` explicitly, since `default_config()`'s
`srun` call has no default account), writing to `myconfig_HI-pawsey.txt`/`myconfig_pawsey-refactor.txt`
rather than touching the user's existing `myconfig.txt` (confirmed byte-identical to a fresh
`pawsey-refactor` `-B` run afterwards, so nothing here altered it). Result: **every config section is
byte-identical between the two branches except `[selfcal]`**, which shows exactly Phase 2's intended
schema change and nothing else — the same `niter`/`threshold`/`calmode`(→`derive_cal`)/`solint` values
losslessly repackaged into the `stages` list. This is real end-to-end confirmation (real field/refant/SPW
auto-detection against real MS metadata, not synthetic input) that Phase 1's `default_config()` migration
is behavior-preserving.

**New feature added to `pawsey-refactor` (not on `HI-pawsey`) while doing this**: `read_ms.py` (the script
`-B` invokes to extract field IDs) now also runs `listobs()` and writes it next to the generated config
(`<config_basename>.listobs.txt`), so every `-B` run produces a human-readable scan/field/spw summary for
free. Verified against the real MS above — `myconfig_pawsey-refactor.listobs.txt` (13KB) has the expected
`listobs` output (Observer, scans, field/intent table, etc.). Adds a few seconds to `-B`'s runtime; no
config-file content changes (confirmed via the `myconfig.txt` diff above).

**One operational quirk found, not a bug**: the `srun`-wrapped `read_ms.py` call `default_config()` makes
for `-B` reliably finishes its actual work (config + listobs written, confirmed via log timestamps) but the
wrapper process/`srun` step itself is slow to exit afterward (observed ~2+ min hang / `CG` completing state)
— harmless (no lingering `squeue` entry once it clears), but don't mistake it for a real hang if scripting
around `-B`.

**Next step (Phase 2)**: the full production-scale 4-stage validation run this section originally called for
is still outstanding (see above) — the smoke test de-risks the stage-list *mechanism* but not
production-scale `tclean`/`gaincal` behavior or Phase 7b's walltime question. `pipe_test_refactor/` is left
in place (jobs 47573195–47573199 completed) for reference/reuse.

**Done (2026-08-25): all of Phase 3.** All three parts landed as separate commits, each verified via
`golden_diff.sh`:
- **`8b92821`** (landed slightly ahead of the rest, while investigating the Phase 2 smoke test's queueing
  problem): stopped force-routing `selfcal_part1`/`selfcal_part2` onto the scarce `long` partition (8 nodes,
  4-day cap) — split `script_registry.py`'s `long_running` flag (which also drives an unrelated `ulimit -n`
  bump) into its own `long_partition` property, left unset for selfcal so it uses whatever `[slurm]
  partition` is configured. `science_image.py` keeps `long_partition=True`, unchanged. Confirmed via
  `golden_diff.sh`: only the two selfcal `.sbatch` files changed (`partition: long` → `work`).
- **`0c8ab55`** (the isolated core-count commit the plan called for): fixed the stale `CPUS_PER_NODE_LIMIT`
  (64 → 128) — confirmed via `scontrol show node`/`sinfo` that Setonix's `work`/`long` nodes are 2×64-core
  sockets (128 physical cores), `ThreadsPerCore=2` (256 logical). Set to the physical, not logical/SMT,
  count (CASA/tclean's FFT-/gridding-heavy work is numerically bound and rarely benefits from
  hyperthreading). Effect: every `cpu_intensive`-only script (not also `is_spw_fanout`, which clamps to 2
  regardless) roughly doubles its requested `cpus-per-task`/`mem` — confirmed via `golden_diff.sh` that the
  diff scope is exactly those 6 scripts and nothing else.
- **`0b4c9f1`**: new `[cluster]` config section (`default_config.txt`) + `CLUSTER_CONFIG_KEYS`, replacing
  the remaining module constants (`TOTAL_NODES_LIMIT`/`MEM_PER_NODE_GB_LIMIT`/`MEM_PER_NODE_GB_LIMIT_HIGHMEM`/
  `MEM_PER_CPU_MB_SHARED`/`DEFAULT_MEM_GB`) and inline `'work'`/`'long'`/`'HighMem'`/`'Devel'` string
  literals in `write_sbatch()`/`write_master()`/`format_args()`, plus the stale Ilifu `account` default in
  `write_jobs()` (`'b03-idia-ag'` → `'pawsey1164'`). New `get_cluster_kwargs(config)` reads `[cluster]` but
  falls back to `DEFAULT_CLUSTER_KWARGS` (mirroring the section's defaults) for a config predating this
  section, via `config_parser.has_section()` rather than hard-requiring it — **confirmed existing configs
  built before this change (no `[cluster]` section) still regenerate correctly via `-R`**, tested against a
  real pre-existing config (not just the fixture). The Python module constants themselves stay (as
  `DEFAULT_CLUSTER_KWARGS`) for argparse CLI defaults/`validate_args()`'s pre-config upper-bound checks,
  mirroring the existing `[slurm]`/`DEFAULT_MEM_GB` duality — deliberately did not touch
  `validate_args()`'s own limit checks, since those run during `-B` before any config file necessarily
  exists. Verified via `golden_diff.sh`: every generated `.sbatch`/`submit_pipeline.sh` file byte-identical;
  the only diff is `.config.tmp` (a verbatim copy of the input config) picking up the new section's content,
  since an equivalent `[cluster]` section was added to the golden-diff fixture too.
- **`bdd5f82`**: consolidated `CONTAINER_PYTHON`/`CONTAINER_ENV`/`CONTAINER_BINDS`/`CONTAINER_PREPEND_ENV`/
  `CONTAINER_MODULES` (five separate container-keyed dicts) into one `ContainerProfile` frozen dataclass
  registry (`CONTAINER_PROFILES`, looked up via `get_container_profile()`) — **stays code, not user config**,
  per the plan's own reasoning: these are deep technical workarounds tied to one specific container build
  (a spack-hash-suffixed OpenSSL path, a source-built `mpi4py` living outside the repo), so if `[slurm]
  container` becomes freely swappable these must not silently stop applying to whatever container a user
  points at instead. Added the called-for `logger.warning` (once per unique unrecognised container path, not
  spammed per-script) when a configured container isn't in the registry. Verified via `golden_diff.sh`: zero
  diff (purely structural); manually confirmed the warning fires once and known-container lookups
  (`idianext.sif`, the SoFiA container) resolve identically to before.

**Done (2026-08-25): Phase 4's `-H`/`--hi_image` flag.** New CLI flag (`c33a46a`), independent of `-I`, same
`remove_roles` gating pattern as `do2GC`/`science_image` — a harmless no-op today since Phase 6 hasn't
landed any script tagged `pipeline_role='hi_image'` yet. `default_config.txt`'s `[image] specmode` default
reverted to `'mfs'`. Verified via `golden_diff.sh` (clean, as expected) and a manual offline `-B`
(`-x`/`--nofields`, no CASA needed) across all four `-I`/`-H` combinations — see `c33a46a`'s commit message
for the exact confirmation.

**Done (2026-08-25): Phase 5 — decoupled `uvsub`/`uvcontsub`, migrated to the new `uvcontsub` task
(`b1a52b5`), and verified against real CASA** (not just syntax/manual review, unlike Phase 2's original
verification — this session has live Setonix access). New `--contsub` flag, independent of `-H`; both gate
`uvsub.py`/`uvcontsub.py` via their already-registered `script_registry` roles. `uvcontsub.py` migrated to
`uvcontsub(vis=vis, outputvis=outputvis, datacolumn='corrected', fitspec=fitspw, fitorder=fitorder,
writemodel=True)`, no longer touches `[data] vis` (writes `[run] hi_contsub_vis` instead, via a new
`bookkeeping.get_hi_contsub_vis()` accessor), and reproduces the old task's `want_cont=True` behavior
explicitly via `split(vis=vis, outputvis=vis+'.cont', datacolumn='model')` → `[run] continuum_vis` →
`bookkeeping.get_continuum_vis()`. Both outputs get a skip-if-exists guard.

Real-CASA smoke test (reusing `pipe_test_refactor/`'s already-selfcal'd, model-populated MMS from the Phase
2 smoke test, job 47580458): confirmed from the actual CASA task log that `uvcontsub`/`split` ran with
exactly the expected parameters and produced both output MSs (`.contsub`, `.cont`); confirmed `[data] vis`
stayed untouched; confirmed `bookkeeping.get_hi_contsub_vis()`/`get_continuum_vis()` read back the correct
paths from the resulting config.

**Found, not fixed (out of Phase 5's scope): a multi-task script whose entire body gets skipped by an
idempotency guard can hang until walltime kill, rather than exiting promptly.** Re-ran the identical
generated `uvcontsub.sbatch` a second time (job 47581620, both outputs already existing) specifically to
confirm the skip-if-exists guard — the guard itself worked correctly (log shows both "already exists. Not
overwriting" messages within ~2s of starting), but the job then hung for the *entire* configured 20-minute
walltime and was killed by SLURM's time limit (`sacct`: `TIMEOUT`, not `COMPLETED`) rather than exiting once
`main()` finished. The first (real-work) run exited normally in 2m17s with no such hang. Root cause not
fully diagnosed, but the pattern points at casampi: `uvcontsub.py` is `threadsafe=True`/`requires_mms=True`
(`split()` parallelizes across an MMS's sub-MSs), so it launches with multiple (here, 9) `srun` tasks; when
neither `uvcontsub()` nor `split()` ever actually gets called, whatever MPI-rank coordination normally
happens inside those task calls (and apparently triggers the other ranks' clean shutdown afterward) never
happens, so the non-rank-0 tasks are left waiting indefinitely. **This is very plausibly not new to Phase
5** — `science_image.py` has the identical `if not os.path.exists(imname): tclean(...)` idempotency pattern
and is also `threadsafe=True`, so a re-run against an already-complete science image may hit the exact same
hang; not confirmed here (would need its own real run to test) but worth checking before relying on
resubmit/resume behavior for any multi-task script with a full-skip idempotency path. Practical impact: on a
real production walltime (hours, not this test's 20 minutes), a routine "resubmit to pick up where it left
off" could silently burn the entire walltime budget doing nothing rather than finishing in seconds — worth
whoever next touches Phase 7's checkpoint/resume design (or resumes any partially-complete run) knowing
about this. `pipe_test_refactor/logs/uvcontsub-47581620.*` has the full log if picking this up later.

**Done (2026-08-25): Phase 6 — HI cube imaging port, plus continuum imaging unification** (`a33a983`; see the
"Phase 6" section below for the full design, arrived at through a planning round with the user that
substantially extended the original scope — robust/uvtaper combos, katbeam PB-correction reuse, per-combo
output dirs, one final SoFiA pass instead of the prototype's three, and unifying `[image]`/`science_image.py`
onto the same stage-list/SoFiA-masking engine as the new `[hi_image]`, renamed `[cont_image]`).

New shared modules `image_stages.py` (CASA-free, mirrors `selfcal_stages.py`)/`image_engine.py`/
`sofia_engine.py`, new entry scripts `hi_image.py`+`hi_sofia.py` ([hi_image]) and `cont_sofia.py`
(`science_image.py` generalized in place, now reading `[cont_image]`), `expand_hi_combo_scripts()`/
`expand_cont_image_stage_scripts()` mirroring `expand_selfcal_loop_scripts()`. Separately ported
PB-correction into selfcal's own final loop (`[selfcal] pb_correct`, default `False`).

**Real end-to-end CASA verification — HI path only**: reusing `pipe_test_refactor/`'s already-contsub'd MMS,
ran the full 2-stage/1-combo chain (dirty tclean → FITS export → SoFiA masking pass → mask import → final
deep clean → fincubes export → final SoFiA source-finding) against real CASA/SoFiA — every step `COMPLETED`,
combo/stage state advanced correctly throughout. Found and fixed 3 real bugs this surfaced (not smoke-test
artifacts): `config_parser.validate_args()` doesn't support `list` dtype (needed direct dict reads for
`stages`/`hi_combos`/`imsize`/`scales`); `hi_sofia.py`/`cont_sofia.py` incorrectly imported `casatasks`
despite running in the CASA-free SoFiA container (FITS export moved to the CASA-side scripts);
`sofia_engine.run_sofia()` didn't check SoFiA's exit code, silently swallowing a failure and letting the
pipeline continue with a mask that was never produced (now raises). Also needed
`importfits(defaultaxes=True, defaultaxesvalues=[...])` explicitly, mirroring
`bookkeeping.get_selfcal_args()`'s existing usermask-import pattern.

**Not verified against real CASA this session**: `science_image.py`'s generalized `[cont_image]` path (only
syntax-checked + `golden_diff.sh`) and selfcal's ported PB-correction — both share the exact code paths
(`image_engine.py`) already proven by the HI smoke test, but the entry-script-level wiring for these two
specifically hasn't been run for real. Worth a real run before trusting them in production, same caveat
pattern as Phase 2's original verification gap.

**Deferred, not implemented this phase** (documented in-code as a seam): a data-processing step before the
final SoFiA pass (common beam to header, spectral axis unit conversion) that the prototype's own fincubes
stage expected — noted in `default_hi_sofmask.txt`'s header comment for whoever adds it next (this template was later merged with the masking-pass template into one shared base file -- see the cleanup note below Phase 8).
`combine_tracks.py` (6c) also not ported — out of scope per this round's Q&A (no new track-combining tool
for continuum in this phase, and the existing HI-dev one wasn't touched).

**Next step**: Phase 7 (parallelism strategy for Setonix's 24h cap) is next in sequence — though the
real-CASA verification gaps just above (`[cont_image]`/selfcal PB-correction) are worth closing first if
picking this up for production use.

**`HI-pawsey`'s `selfcal_part1` crash is resolved** (as of `HI-pawsey` commit `543363b`, cherry-picked here
as `a062602`). Phase 2's write-up below still contains a "Correction (2026-08-22...)" callout describing an
intermediate state where the first fix attempt (`0013ebe`, forcing `calcpsf=True`) turned out *not* to fix
the crash — that callout is now superseded by the real root cause and fix described right after it
(leftover stale `imagename.psf`/`.sumwt` files from a previous crashed attempt confusing `tclean`'s
`restart=True` path regardless of `calcpsf`; fixed by deleting `imagename.*` before every `tclean` call in
`selfcal_part1.py`).

**Update (2026-08-25, on-disk state check)**: the paragraph below (and the "Next step" above it) was written
assuming loop 3 hadn't run yet. Checking `/scratch/pawsey1164/ssankar/pipe_test/` directly shows `HI-pawsey`'s
reference run has since gone further than this doc tracked: **the full 4-stage HI loop completed
end-to-end**, including loop 3 (the `niter=1000000` deep clean) — `1738276790.NGC4064_im_3.image` exists
(mtime 2026-08-23 13:55), and `run_sofia.py`'s continuum-subtraction masking ran after it
(`_im_3_cat.txt`/`_mask.fits`/`_rel.eps`, mtime 2026-08-23 14:07). So Phase 7b's 24h-walltime-cap concern for
loop 3 did not materialize on this MS — worth knowing when Phase 7b is actually designed, though not
conclusive for a larger MS/longer deep clean. `uvsub.py`/`uvcontsub.py`/`science_image.py` had not produced
output yet as of this check (only their generated `.sbatch` files exist) — the run had not reached that far,
or stalled before it; check `squeue`/job logs before assuming it's still progressing. Phase 2's stage-list
restructuring is unaffected either way (it was always a readability win independent of the bug); Phase 7b's
checkpoint-chaining design should still apply the same "always clean up, never assume leftover state is safe
to reuse" lesson when it's implemented, even though the specific bug that taught it is now fixed.

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

**Addendum (2026-08-25, from the user, before implementation started)**: users need to compare cubes across
multiple `robust`/`uvtaper` weighting choices (a common HI trade-off between sensitivity and resolution),
so `robust` and `uvtaper` are **not** plain `[hi_image]` scalars like the prototype's other imaging settings
— they're driven by a new `hi_combos` list, one dict per combination, following exactly the `[selfcal]
stages` list precedent from Phase 2 (a list of dicts rather than parallel arrays, for the same reason: Phase
2 moved away from parallel arrays specifically because they drift out of sync):
```python
hi_combos = [{'robust': -0.5, 'uvtaper': ''},
             {'robust': 2.0,  'uvtaper': '30arcsec'}]
```
For **each** entry in `hi_combos`, the *entire* 6a→6b sequence (m2h0 dirty cube + SoFiA mask, then m2h1
final deep clean using that combo's own mask) runs independently end-to-end — no mask-sharing/reuse
shortcut across combos, even though a mask computed at one weighting might look similar to another; the
combo's own dirty cube and its own SoFiA mask are what feed its own deep clean. This is a second, orthogonal
axis from the existing `hi_niter`/`hi_threshold` 2-element paired lists (which index by *stage within one
combo* — `[0]` for m2h0's dirty clean, `[1]` for m2h1's deep clean — and stay constant across combos in this
initial cut; a future per-combo niter/threshold override is a plausible follow-up but not required now).
Every other `[hi_image]` setting (`scales`/`gridder`/`wprojplanes`/`deconvolver`/`weighting`) stays a single
scalar shared across all combos, unaffected by this addendum.

Output naming: suffix imagenames with the combo's index (`_hi{c}_m2h0`/`_hi{c}_m2h1`, 0-based, mirroring
Phase 2's `_im_%d` per-loop convention) rather than embedding the `robust`/`uvtaper` values themselves in
the filename — `uvtaper` in particular can be `''` or contain characters (e.g. `'30arcsec'`) that are
awkward or ambiguous in a filename, and two combos could in principle share a `robust` value with different
`uvtaper`. Log the actual `(robust, uvtaper)` pair being used at the start of each combo's processing so the
index-to-parameters mapping is easy to recover from the log even though it's not in the filename.

Mechanism: mirror `expand_selfcal_loop_scripts()`'s existing pattern (Phase 1/2) rather than inventing a new
one — a new `expand_hi_combo_scripts()` replicates the m2h0-mask-m2h1 script-tuple sequence `len(hi_combos)`
times in `postcal_scripts`, the same way self-cal loops get expanded from a `[selfcal] stages`-derived
count. `combine_tracks.py` (6c) is **not** combo-aware in this initial cut (the addendum only calls out the
m2h0+mask+m2h1 loop) — worth flagging as a likely follow-up once 6c is actually built, since it will need to
know which combo's outputs it's combining across two tracks, but out of scope for this addendum.

**As shipped (2026-08-25, `a33a983`) — superseding 6a/6b/6c below after a planning round with the user that
substantially revised the design.** Kept for history; see the "Done: Phase 6" status entry above for
verification details.

- Reuses `science_image.py`'s existing katbeam `do_pb_corr()` instead of the prototype's own
  `vp.setpbnumeric`/`vptable` PB model — moved to the new shared `image_engine.py` so both HI and continuum
  imaging (and selfcal's final loop, separately) call the same correction. PB-correction is opt-in
  (`pb_correct`, default `False`), not unconditional like the prototype.
  `immoments` dropped entirely, and the prototype's *second* SoFiA pass (on the un-rebinned final image)
  dropped too — only a masking pass (between stages) and one final source-finding pass (post-export)
  remain, down from the prototype's three SoFiA passes.
- 6a/6b's dirty-cube/final-deep-clean split collapsed into one stage-list-driven engine
  (`image_stages.Stage`: `mask`/`niter`/`threshold`, exactly `[selfcal] stages`'s shape) rather than two
  fixed steps — `hi_niter`/`hi_threshold` paired lists never shipped; `[hi_image] stages` (a list of stage
  dicts) replaced that plan outright. `hi_image.py` (imaging) + `hi_sofia.py` (masking/final SoFiA passes)
  are the only two new script files, both thin wrappers sharing `image_engine.py`/`sofia_engine.py` with
  **continuum** imaging too: `[image]` was renamed `[cont_image]` and `science_image.py` (formerly one fixed
  `tclean` call) generalized to the same stage/mask-aware design, paired with new `cont_sofia.py` — not
  originally planned, added per the user's direction that continuum and HI imaging should run "the same
  scripts with different parameters." `restfreq`/`imspw` reused from `[cont_image]` (renamed from `[image]`
  per the above, not left in a separate `[image]` section as 6a originally described).
- Output directory structure: `hi_combo<N>/` per combo (not per-parameter-value naming — see the addendum
  above), containing `stage<N>.image` etc. per stage and a `hi_combo<N>/fincubes/` subdir for the final
  exported (optionally rebinned) product — `cont_image/` (no combo axis) for continuum.
- Order after the final stage's `tclean`: optional `imrebin` → optional PB-correction → `fincubes`-style
  export (export works on non-PB-corrected data, so it isn't gated on PB-correction having run) — corrected
  from the prototype's own interleaved order per the user's direction.
- **6c (`combine_tracks.py`) not ported this phase** — confirmed out of scope: no continuum equivalent
  needed, and the existing HI-dev prototype version wasn't touched or copied in.
- **Deferred, not implemented**: a data-processing step the prototype's fincubes stage expected before its
  final SoFiA pass (common beam to header, spectral axis unit conversion) — noted as an in-code seam in
  `default_hi_sofmask.txt`'s header comment for whoever adds it next (this template was later merged with the masking-pass template into one shared base file -- see the cleanup note below Phase 8). `exportfits` also deliberately
  keeps native frequency units now (dropped the prototype's `velocity=True, optical=True`), per the user's
  direction that this belongs in that same future post-processing step.
- New `script_registry` roles (`hi_image`, `hi_sofia`, `cont_sofia`), gated behind `-H`/`-I` respectively via
  Phase 4's registry-driven `remove_roles`, and expanded via `expand_hi_combo_scripts()`/
  `expand_cont_image_stage_scripts()` (flat replication — every stage needs its own SoFiA pass, unlike
  `expand_selfcal_loop_scripts()`'s special-cased final loop).

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

**Post-Phase-6 cleanup (not a numbered phase)**: `default_hi_sofmask_mask.txt`/`default_hi_sofmask_final.txt`
(Phase 6's two ~90%-identical SoFiA templates) were consolidated into one shared `default_hi_sofmask.txt` base
template, with the masking-pass/final-pass differences (S+C kernels, reliability threshold, which output
products get written) now expressed as `[hi_image]`/`[cont_image]` config-driven overrides
(`sofia_mask_params`/`sofia_final_params`, applied at runtime by `sofia_engine.run_pass()`'s new `overrides`
argument) instead of baked into two separate template files. Same cleanup round also: dropped `[cont_image]`'s
unused `imspw` (continuum imaging now always uses `spw=''`); moved uvcontsub.py's `fitspw`/`fitorder` out of
`[cont_image]` into their own `[contsub]` section; fixed `selfcal_part2.mask_image()` silently overwriting
`run_sofia.py`'s SoFiA mask with PyBDSF's own auto-mask in the final SoFiA-driven selfcal loop; and
de-duplicated `aux_scripts/run_sofia.py`'s local SoFiA-invocation helpers into `sofia_engine.py` (which also
fixed a silent-failure gap — SoFiA exiting 0 on an internal failure now raises instead of continuing).

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
