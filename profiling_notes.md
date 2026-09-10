# Profiling notes

## pipe_test — 10 MHz HI band test run

Test dataset: `1738276790.ms` (MeerKAT L-band, 1024 channels, native channel width
835937.5 Hz, full band 856.31–1711.48 MHz).

Selected band for this pipeline run: `spw = '*:1410~1420.0MHz'`, `chanbin = 1` (no
averaging during partition).

**X = 12 channels** in the 10 MHz band (10.03125 MHz / 835937.5 Hz per channel = 12
channels; confirmed directly via `msmetadata.nchan()`/`chanwidths()` on
`1738276790.1410~1420.0MHz.NGC4064.mms`, not just derived from bandwidth/width alone).

## Wall time per successful script (`work` partition, 128-physical-core Setonix nodes)

Times below are for the run's canonical *successful* completion of each stage — several
stages were retried during debugging this session (container/MPI/mask fixes); those
retries are excluded here as not representative of steady-state timing. `AllocCPUS` is
total cores allocated to the job (`nodes x ntasks-per-node x cpus-per-task`).

| Stage | Job ID | AllocCPUS | Elapsed | Notes |
|---|---|---|---|---|
| `validate_input` | 47410535 | 36 | 00:00:20 | |
| `flag_round_1` | 47410536 | 126 | 00:01:09 | |
| `setjy` | 47410537 | 36 | 00:01:05 | |
| `xx_yy_solve` (round 1) | 47410538 | 36 | 00:00:55 | |
| `xx_yy_apply` (round 1) | 47410539 | 36 | 00:00:53 | |
| `flag_round_2` | 47410541 | 126 | 00:01:12 | |
| `xx_yy_solve` (round 2) | 47410542 | 36 | 00:00:57 | |
| `xx_yy_apply` (round 2) | 47410543 | 36 | 00:00:53 | |
| `split` | 47410544 | 36 | 00:01:18 | |
| `quick_tclean` | 47434456 | 126 | 00:04:45 | 1 prior failed attempt (pre-container-fix) |
| `concat` | 47434571 | 36 | 00:00:19 | |
| `plotcal_spw` | 47434572 | 36 | 00:00:20 | |
| `selfcal_part1` loop 0 (dirty, niter=10000) | 47434973 | 256 | 00:41:28 | ran on `work` directly (see below) |
| `selfcal_part2` loop 0 (bdsf + mask) | 47436565 | 128 | 00:02:53 | 1 prior failed attempt |
| `selfcal_part1` loop 1 (phase target, niter=50000) | 47445556 | 256 | 01:02:03 | 3 prior failed attempts (the `calcpsf`/stale-file bug) |
| `selfcal_part2` loop 1 | 47445557 | 128 | 00:07:24 | |
| `selfcal_part1` loop 2 (amp+phase target, niter=80000) | 47449363 | 256 | 01:38:36 | stopped early at iteration 18 (threshold reached fast) |
| `selfcal_part2` loop 2 | 47449364 | 128 | 00:07:10 | |
| `selfcal_part1` loop 3 (deep clean, niter=1000000) | *not yet run* | | | most walltime-relevant for Phase 7b's 24h cap |

`selfcal_part1`'s `--exclusive` node reservation gives it the full node (256 logical
CPUs / 128 physical cores via SMT), rounded to the nearest power of two for
`--ntasks-per-node` (8 tasks x 32 cpus-per-task here).

## Early signal for Phase 7 (parallelism strategy)

Breakdown of `selfcal_part1` loop 1's successful run (job 47445556, total 1h02m03s),
from the CASA log timestamps — this is the level of detail we'll want for real profiling:

- **Setup** (`Verifying Input Parameters` → first `Running Parallel Major Cycle`):
  15:27:42 → 15:39:45 = **~12 min**. Consistent with the ~11-13 min setup/PSF/weight-density
  overhead seen at the same point in every run this session, including the failed ones —
  this looks like fixed per-invocation cost, not scaling with `niter`.
- **8 major/minor cycle iterations** (`Running Parallel Major Cycle` → next one, or final
  stopping criterion), highly non-uniform: 1m31s, 7m11s, **16m55s**, 45s, **17m55s**, 46s,
  3m36s, 18s (reached threshold at iteration 360, stopped after 361 total). The two long
  ~17min cycles dominate; several others are near-instant. Total cycling time ~49m — most
  of the hour, not the setup.
- Deconvolution itself (`MatrixCleaner::clean()`'s minor-cycle loop) is fast relative to the
  major cycle (gridding/degridding over the full 6144x6144, wprojplanes=512 image) — the
  long cycles above are dominated by the major cycle, not minor-cycle iteration count.

Loop 0 (dirty, niter=10000, no mask) took 41m28s in one shot with no comparable log-level
breakdown recorded yet — worth re-deriving the same way if precise numbers are needed.
Loop 3 (niter=1000000, deep clean) is the one to watch against the `work` partition's 24h
cap — it won't stop early on iteration count the way loop 1 did (loop 1 hit its `threshold`
well under budget), so its major-cycle-count and per-cycle cost are the key unknowns for
Phase 7b's checkpoint-chaining design. Worth capturing this same cycle-by-cycle breakdown
once loop 3 runs.

## HI_p1 — production run (NGC4064, real full-scale MS)

Real production run, not a scaled-down smoke test — findings here directly informed
`default_config.txt`/`correlator_modes.py`'s current defaults, not just documentation.

### selfcal_part1 OOM investigation and profiled safe operating point

`selfcal_part1.py` (the `--exclusive`, MPI-parallel deep-clean `tclean` step) OOM'd twice at
the *cheapest* selfcal loop (loop 0, `niter=10000`): once at 1 node/16 tasks
(`--mem=230GB` exclusive), once at 2 nodes/16 tasks/node (~460GB aggregate). Both died in
the same place: right after `SynthesisDeconvolver::setupDeconvolution` during PSF/weight-
density gather-scatter, not during the major cycle itself.

**Root cause**: `tclean`'s MPI parallelism (`casampi`) only splits *visibility* data across
ranks — each rank's *image-side* buffers (PSF, weight density, multiscale deconvolution
buffers for `scales=[0,5,10,15]`, w-projection kernels for `wprojplanes=512`) are
replicated in full on every rank. Adding more nodes at the same ranks-per-node doesn't help
this component — confirmed by the 2-node/16-task test: it OOM'd on one specific node, since
SLURM `--mem` is strictly per-node, so the second node's spare capacity was never available
to the node that died. The real lever is *ranks-per-node* (fewer ranks sharing one node's
fixed 230GB budget), not total node count.

**Fixes applied** (now the pipeline-wide defaults in `default_config.txt`, and in the live
`HI_p1` config): `[selfcal] wprojplanes` 512→128 (matches what `[cont_image]` already used),
`[selfcal] scales` synced from stale `[0,5,10,15]` to `[0,5,10]`.

**Profiled safe operating point** (real `sacct` `TRESUsageInTot` data, not estimates):

| Config | Result |
|---|---|
| 1 node / 16 tasks, `wprojplanes=512`, `scales=[0,5,10,15]` | OOM'd, ~260GB/node at last sample (over 230GB budget) |
| 2 nodes / 16 tasks-per-node, same | OOM'd on one node (~260.5GB/node at last sample — confirms per-node ranks, not node count, is the driver) |
| **2 nodes / 8 tasks-per-node, `wprojplanes=128`, `scales=[0,5,10]`** | **Succeeded, all 4 loops** |

All 4 selfcal loops at the safe point, real `sacct` `TRESUsageInTot`/16 ranks → GiB/node:

| Loop | `niter` | Elapsed | Aggregate/node (8 ranks) | Headroom vs 230GiB |
|---|---|---|---|---|
| 0 | 10,000 | 3h56m | 207.4 GiB | 9.8% |
| 1 | 50,000 | 4h11m | 199.1 GiB | 13.4% |
| 2 | 80,000 | 6h39m | 204.8 GiB | 10.9% |
| 3 (final, deep clean) | 1,000,000 | 11h31m | 203.2 GiB | 11.7% |

**Confirmed: memory does NOT grow with `niter`/loop depth** — all 4 loops (0 through the
original OOM target, `niter=1,000,000`) land in the same ~199-207 GiB/node band. **2 nodes /
8 tasks-per-node is the confirmed safe operating point across the full run**, not just the
cheap early loops — now encoded as `correlator_modes.py`'s `32K_NE107M` mode entry
(`slurm: {'selfcal_part1': {'nodes': 2, 'ntasks_per_node': 8}}`, applied automatically via
`slurm_config_registry.py` whenever that correlator mode is identified), kept at this
measured ~10-13% headroom rather than shaved any tighter.

**`selfcal_part2.py`'s `230GB` request is a heuristic artifact, not a real need** — it's
single-task/single-node (not MPI, not `--exclusive`); the `230GB` comes from
`script_registry.py`'s `cpu_intensive=True` pulling `cpus-per-task` to all 128 cores, which
then computes `230GB` via the shared node mem/cpu ratio. Real profiled usage (`sacct`
`TRESUsageInMax`): loop 0's skip-branch (`derive_cal==''`, only PyBDSF + lightweight CASA
mask ops) — **4.96 GB against the 230GB request, ~2.2% utilization**. Loops with
`derive_cal != ''` (1-2) or the final loop additionally run a non-parallel predict-only
`tclean(niter=0, savemodel='modelcolumn')` + `gaincal`: 38.3 GiB (loop 1), 41.2 GiB (loop
2), also fine for the final/predict branch. Right-sizing this request is a real, easy,
independent optimization (not yet implemented — see REFACTOR_PLAN.md's Phase 9 write-up).

### HI cube imaging (`hi_image.py`) — parallel-vs-serial test, and the 402GB memory warning

Full-scope (`imspw` spanning the whole ~21MHz crosscal band, `wprojplanes=256`) `hi_image.py`
run: CASA's own memory estimator reported "Required memory 401983440 kB (~402GB) vs
Available mem 241172480 kB (~241GB)" — under-provisioned by ~1.67x at 1 node/230GB. This is
not automatically fatal (CASA auto-chunks into subcubes to fit budget), but is a real signal
the full-scope job needs either a narrower band or more memory.

A real ~20h side-by-side serial-vs-parallel test (narrowed scope: `imspw`~7MHz,
`wprojplanes=128`, `rebin=True`; 2 nodes × 8 tasks for the parallel run) found:
- **Zero MPI/tclean errors in either run** — confirms the older CASA cube-mode MPI bug
  `image_engine.py`'s `parallel=False` workaround existed for is fixed on this container's
  CASA version; the workaround has since been removed (see REFACTOR_PLAN.md's Phase 9).
- **The serial/parallel wall-clock gap narrowed from ~18-20% (small chunks, early on) to
  ~2% (large chunks, later)** — consistent with MPI coordination being a roughly *fixed*
  per-invocation cost that matters proportionally less as each rank's channel range grows,
  not a genuine per-unit-of-work speedup at this scale. I.e. **more MPI ranks is not the
  lever for HI cube imaging speed** — narrower `imspw`/lower `wprojplanes` (both already
  applied as defaults) are the real levers, along with walltime.
- **Neither run completed a single stage within 24h**, even at the narrowed scope — no
  confirmed-safe `hi_image.py` resource/walltime point exists yet (unlike `selfcal_part1`'s
  above). Not yet added to `correlator_modes.py`'s `slurm` entry for this reason — see
  REFACTOR_PLAN.md's Phase 9 ToDo.

### Two real config-generation bugs found and fixed via real `-B` runs against this MS

- **Correlator-mode identification matched `(nchan, total bandwidth)`, which is wrong** — a
  delivered MS is often a spectral *subset* of a correlator mode's full native band (this
  MS: 6127 channels / 20.0MHz, not `32K_NE107M`'s full 32768/107MHz — MeerKAT's SDP only
  archived a window around the target line). Fixed to match on native **channel width**
  alone (`ChanWid`, invariant regardless of delivery window) — see `correlator_modes.py`.
- **`read_ms.py`'s `check_scans()` targeted `nscans/2`, undershooting** — `do_partition()`
  creates exactly one sub-MS per scan (`mstransform(..., numsubms=msmd.nscans(), ...)`), so
  the natural parallelism is one rank per scan (`nscans` itself), not half. Confirmed via
  this MS's real 18-scan count producing a clearly-too-low "1 node/9 tasks" recommendation
  before the fix. Fixed to target `nscans` directly.

## P1_test — chanbin=2 crosscal chain vs. HI_p1's chanbin=1, same MS

`P1_test` (started 2026-09-08) processes the *same* real MS as `HI_p1`
(`N4064_HI/1738276794/...mmu.ms`, same 18 scans, same `32K_NE107M` correlator mode) but is
the first real run to pick up `correlator_modes.py`'s `chanbin=2` default for this mode —
`HI_p1`'s own crosscal chain predates `correlator_modes.py` entirely and was built with
`chanbin=1` (no averaging), either manually or from the pipeline's pre-Phase-9 plain
default. Comparing the two real `sacct` histories for one matching SPW
(`1404~1409MHz` / `1404.5037~1409.5037MHz`) gives a genuine before/after on the new default,
with one important confound called out below.

| Stage | HI_p1 (`chanbin=1`) Elapsed | nodes×tasks | mem (`TRESUsageInTot`, aggregate) | P1_test (`chanbin=2`) Elapsed | nodes×tasks | mem |
|---|---|---|---|---|---|---|
| `partition` | 43:49 | 1×18 | 60.1 GiB | 03:29 | 2×32 | 58.8 GiB |
| `validate_input` | 00:19 | 1×1 | 0.7 GiB | 00:17 | 1×1 | 0.7 GiB |
| `flag_round_1` | 13:31 | 1×18 | 122.7 GiB | 05:32 | 2×32 | 77.1 GiB |
| `setjy` | 28:20 | 1×18 | 22.7 GiB | 03:22 | 2×32 | 19.8 GiB |
| `xx_yy_solve` (r1) | 04:44 | 1×1 | 9.5 GiB | 02:40 | 1×1 | 5.9 GiB |
| `xx_yy_apply` (r1) | 21:14 | 1×18 | 61.1 GiB | 02:24 | 2×32 | 39.7 GiB |
| `flag_round_2` | 26:47 | 1×18 | 133.1 GiB | 12:29 | 2×32 | 82.0 GiB |
| `xx_yy_solve` (r2) | 05:09 | 1×1 | 9.6 GiB | 02:49 | 1×1 | 8.1 GiB |
| `xx_yy_apply` (r2) | 16:00 | 1×18 | 30.5 GiB | 02:06 | 2×32 | 29.1 GiB |
| `split` | 21:43 | 1×18 | 94.7 GiB | 02:06 | 2×32 | 50.4 GiB |
| `quick_tclean` | 19:35 | 1×18 | 48.5 GiB | 11:36 | 2×32 | 49.7 GiB |

**Two factors differ between these runs, not one — don't attribute the whole speedup to
`chanbin` alone.** `[slurm] ntasks_per_node` also differs (8 for `HI_p1`'s crosscal chain,
16 for `P1_test`'s — both auto-derived by `read_ms.py`'s `check_scans()` from this MS's own
18-scan count at each run's independent `-B` time, landing on a different achievable tier
each time per the `check_scans()` behavior described just above; nothing about `chanbin`
drives this). So every per-SPW-parallel step (`partition`/`flag_round_1`/`setjy`/
`xx_yy_apply`/`flag_round_2`/`split`/`quick_tclean`, all `NNodes×NTasks` = 1×18 vs. 2×32
above) has *both* half the channel data *and* ~1.8x the tasks working on it in `P1_test`.

The cleanest chanbin-only signal is `xx_yy_solve` (single-task, not threadsafe, identical
`1×1` task shape in both runs): **04:44 → 02:40 elapsed (~1.8x faster), 9.5 → 5.9 GiB
(~1.6x less memory)** — both roughly consistent with half the channel count, as expected
from `chanbin=2`'s channel-averaging happening during `partition.py`'s `mstransform`.
`validate_input` (also single-task, and doesn't touch channel data) is within noise between
the two runs (0.7 GiB either way), confirming the `xx_yy_solve` gap is a real chanbin effect
and not just run-to-run variance.

For the multi-task steps, both effects compound: `partition` itself is where `chanaverage`
actually happens (`chanaverage=True if preavg>1`), so it directly processes half the output
channels *and* got 78% more tasks — 43:49 → 03:29 (~12.6x). `flag_round_1`/`flag_round_2`
(the two most expensive per-SPW steps) dropped 13:31→05:32 and 26:47→12:29 respectively
(~2.4x and ~2.1x) while aggregate memory fell too (122.7→77.1 GiB, 133.1→82.0 GiB) — memory
falling *despite* ~1.8x more tasks is itself further evidence the per-task data volume
genuinely shrank, not just that more parallelism papered over the same total work.
`quick_tclean` is the outlier: memory is essentially flat (48.5 vs 49.7 GiB) since it images
a fixed field-of-view regardless of channel count, and its wall-time drop (19:35→11:36,
~1.7x) tracks the task-count increase more than `chanbin`.

**Not a controlled experiment — worth re-running properly if this distinction ever matters**
(e.g. for a future correlator-mode `MODES` entry's `chanbin` choice specifically): pin
`[slurm] ntasks_per_node` to the same value across a `chanbin=1` and `chanbin=2` build of
the same MS to isolate `chanbin`'s effect alone from `check_scans()`'s independent
auto-sizing. As-is, both real runs agree directionally (chanbin=2 is faster and lighter on
memory per task, exactly as expected from processing half the channels) but the *magnitude*
of the combined win above (2-12x depending on stage) should not be read as "chanbin=2 alone
gives Nx" — a meaningful chunk of it is the extra tasks.

### Self-cal (`selfcal_part1`, all 4 loops): a milder, still-real chanbin effect

`P1_test` completed all 4 self-cal loops since the crosscal comparison above was written.
Same MS, same `2 nodes/8 tasks-per-node` profiled operating point on both sides (this part
of the config is pinned by `correlator_modes.py`'s `slurm` override regardless of `chanbin`,
so — unlike the crosscal comparison above — this pairing is *not* confounded by a
`check_scans()`-driven task-count difference):

| Loop | `niter` | HI_p1 (`chanbin=1`) `selfcal_part1` Elapsed | P1_test (`chanbin=2`) `selfcal_part1` Elapsed | Speedup |
|---|---|---|---|---|
| 0 | 10,000 | 3h56m | 1h21m00s | ~2.9x |
| 1 | 50,000 | 4h11m | 2h10m50s | ~1.9x |
| 2 | 80,000 | 6h39m | 3h37m25s | ~1.8x |
| 3 (final, deep clean) | 1,000,000 | 11h31m | 7h15m33s | ~1.6x |

Real, consistent speedup, but far milder than crosscal's 2-12x and — as the next section
shows — dramatically milder than `hi_image`'s. Consistent with `[selfcal]` imaging at
`specmode='mfs'` (the default; not overridden in either run's config): a continuum/MFS clean
collapses every channel into one output plane, so `chanbin` still roughly halves the number
of channels gridded into that plane, but there's no per-channel-plane or subcube-chunking
multiplier the way cube mode has (see below) — the win here is closer to the "clean"
single-factor `xx_yy_solve` signal from the crosscal comparison than to crosscal's
compounded multi-task numbers.

### `hi_image` (cube mode): the same `chanbin` factor, massively amplified by CASA's subcube memory-chunking

This is the finding behind "it's remarkable this didn't stall like HI_p1's test did" — dug
out of both runs' actual CASA logs, not inferred from `sacct` alone.

**`HI_p1`'s own `hi_image.py` test previously stalled, not just ran slowly.** Job 47820395
(2026-08-30, `test_hi_image_parallel.py` — the "real ~20h side-by-side serial-vs-parallel
test" referenced above, narrowed scope: `imspw`~7MHz, `wprojplanes=128`, `rebin=True`, 2
nodes x 8 tasks) hit its 24h `work` walltime cap having completed only **5 major cycles** in
the full 24 hours — confirmed directly from its `.err` log, which shows exactly 5
`0%....100%` tclean progress bars and nothing further before the `DUE TO TIME LIMIT`
cancellation. That's ~4.8h/cycle. (This run predates `correlator_modes.py` by one day —
Phase 9, which introduced the `chanbin=2` default, landed 2026-08-31 — so it necessarily
used `chanbin=1`, the plain pre-Phase-9 default, over the same `~7MHz` window.)

**`P1_test`'s `hi_image` (job 48228936, the same `imspw`~6MHz/`wprojplanes=128`/`rebin=True`
config, now with `chanbin=2`) cycles roughly every ~63 minutes**, timed precisely from the
`Run Major Cycle N` log timestamps (cycle-start-to-cycle-start, cycles 1-5):
53m34s, 62m30s, 72m34s, 63m37s — avg **63m04s/cycle**, plus ~49min one-time setup before
cycle 1. At that pace it completes ~23 cycles in 24h vs. `HI_p1`'s 5 — **~4.6x more cycles
in the same wall-clock budget.**

**Root cause, confirmed directly from the `tclean` call in `P1_test`'s log**: cube shape is
`[2048, 2048, 1, 919]`, channel increment `6530.33 Hz` — `chanbin=2`'s doubled channel width
baked directly into the cube. At `HI_p1`'s `chanbin=1` over the same ~7MHz window and the
native ~3265Hz channel width, that's roughly **~2,100 channels, ~2.3x `P1_test`'s 919.**

**Why a ~2.3x channel-count difference produces a ~4.6x cycle-rate difference, not ~2.3x**:
CASA's own memory estimator (`SynthesisImagerVi2::nSubCubeFitInMemory`) scales close to
linearly with channel count, and when the estimated requirement exceeds the node's budget it
splits each major cycle into that many sequential subcube passes. `P1_test`'s 919-channel
cube needs "Required memory: 353.1 GB ... Subcubes: 2" against 184GB available. A directly
adjacent `HI_p1` log (job 47791034, an earlier *full-band* `wprojplanes=256` attempt,
`chanbin=1`, 6127 channels — confirms the linear scaling empirically: "Required memory: 2354
GB ... **Subcubes: 13**") shows this mechanism concretely, though it's a different `imspw`
scope than 47820395's own narrowed test, whose detailed CASA log wasn't preserved (only its
`.err` progress bars survived) — so the *exact* subcube count for the identical narrowed,
chanbin=1 test is inferred (interpolating the confirmed linear relationship to ~2,100
channels lands around 4-5 subcubes), not measured on that specific run. The direction and
mechanism are solid regardless: doubling channel count doesn't just double the gridding work
per plane, it can push the subcube count up a tier, and every extra subcube is a full extra
sequential pass *within* every major cycle — compounding rather than adding.

**Nothing changed in `image_engine.py`'s cube-imaging code path between these two runs** —
same logic that stalled on `HI_p1`. What changed is that `P1_test` is the first real run to
inherit `correlator_modes.py`'s `chanbin=2` default, which roughly halves the channel count
feeding `hi_image.py`'s cube, landing in a regime where CASA's subcube chunking goes from
double digits down to 2. Worth remembering when `correlator_modes.py` gains a second `MODES`
entry someday: `chanbin`'s effect on cube-mode HI imaging specifically is not the same
mild, roughly-linear win seen in crosscal/selfcal above — it can cross a subcube-count
threshold and produce a much larger, non-linear effect.

**Update — both stages now finished (or, for stage 1, ran out its walltime) — and stage 1
surfaced a real hang, not just a long runtime.**

`hi_image` stage 0 (`niter=50000`) **completed successfully: 7h00m50s** — closely matching
the cycle-rate extrapolation above (5 cycles × ~63min + ~49min setup ≈ 6h44m). `hi_sofia`'s
masking pass between stages completed in 13m54s. Both clean, no errors.

`hi_image` stage 1 (`niter=1,500,000`, the deep clean — the highest-`niter`, most
walltime-relevant stage) **did not complete: TIMEOUT at 12h00m27s**, the sbatch's configured
`--time=12:00:00` limit. The real per-cycle timestamps (`Run Major Cycle N` in its CASA log)
give a precise picture:

| Cycle | Start | Interval from previous |
|---|---|---|
| 1 | 18:02:14 | — |
| 2 | 18:56:27 | 54m13s |
| 3 | 19:52:39 | 56m12s |
| 4 | 20:50:38 | 57m59s |
| 5 | 21:46:50 | 56m12s |
| 6 | 22:46:18 | 59m28s |
| 7 | 23:46:17 | 59m59s |
| 8 | 00:49:13 | 62m56s |
| 9 | 01:53:54 | 64m41s |
| 10 | 03:00:03 | 66m09s |
| 11 | 04:06:34 | 66m31s |

So loop-depth/`niter` **does** appear to affect `hi_image`'s per-cycle cadence, unlike
`selfcal_part1`'s flat per-node memory across loops (HI_p1 section above) — cadence crept
from 54min to 66min/cycle fairly steadily across the run (~1-2min slower each cycle), plausibly
tracking the mask/model growing more complex as deconvolution progresses. Worth confirming
with a second run before treating this as settled, since it's one data point.

**The real finding: cycle 11 didn't just run long, it hung.** It started normally at 04:06:34,
produced log output at the expected pace through 05:10:23 (into `MatrixCleaner::clean()`'s
minor-cycle loop, iteration 1600 of what should be a routine few-thousand-iteration pass —
compare `selfcal_part1`'s minor cycles, which the HI_p1 section above found fast relative to
major-cycle cost), and then **produced zero further log output for the remaining ~8 hours**
before SLURM killed it at the 12h limit — confirmed via the log file's content timestamp
(last line: 05:10:23) vs. its filesystem mtime (13:10:23, when the kill flushed/closed it).
Not a slow finish; a genuine stall partway through ordinary deconvolution work.

This is a *different* manifestation of the casampi-coordination-hang risk first flagged in
Phase 5 (`REFACTOR_PLAN.md`) for `uvcontsub.py` — that one was specifically a full-skip
idempotency path (non-rank-0 ranks left waiting because the guarded work, and whatever
rank-coordination normally happens inside it, never ran at all). This hang happened
*mid-genuine-computation*, with real work already done in the same job — a broader, more
concerning failure mode than "skip-guard hides a coordination step," since it means an
actively-progressing `hi_image` run can still silently stall with no error, no traceback, and
no indication anything is wrong until walltime kills it. Worth prioritizing root-causing this
specifically (rank hang during `tclean`'s minor cycle under MPI, on this container's CASA
version) before Phase 7b's checkpoint-chaining design is finalized — a checkpoint/resume
mechanism built only around "walltime naturally runs out" doesn't help if the job can stall
productively-idle for most of an allocation first. See REFACTOR_PLAN.md's Status section for
where this stands.
