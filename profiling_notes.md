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
