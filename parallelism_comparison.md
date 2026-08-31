# Parallelism strategies for processMeerKAT on Setonix

Comparison of three distinct parallelism strategies relevant to this pipeline, based on:
- IDIA pipeline docs: https://idia-pipelines.github.io/docs/processMeerKAT/SLURM-and-MPICASA/
- CASA parallel processing docs: https://casadocs.readthedocs.io/en/stable/notebooks/parallel-processing.html#Parallel-Imaging
- CASA synthesis-imaging docs, "Imager Parallelization": https://casadocs.readthedocs.io/en/stable/notebooks/synthesis_imaging.html
- CASA Memo 13, "Cube Parallelization with CASA6" (Sekhar, Rau & Xue, Aug 2024) https://drive.google.com/file/d/1_8JeN-MtDEqUYjRn7eIUbqcE0EyJeSqW/view

## 1. MPI/casampi task-internal parallelism (what this pipeline currently uses)

Each SLURM task launched via `srun -n N` becomes one MPI rank. Rank 0 is the "client"
(runs the actual script), ranks 1..N-1 are "servers" sitting inside `casampi`'s command-dispatch
loop waiting for work. The `threadsafe` flag on each script in the pipeline's config
(`[slurm] scripts = [(name, threadsafe, container), ...]`) controls whether that script gets
`ntasks_per_node` (multi-rank MPI) or a single task.

Used by: `partition.py` (`mstransform(createmms=True)`), `flag_round_*`, `setjy`, `xx_yy_apply`,
`split`, `quick_tclean`, `selfcal_part1`, `uvcontsub`, `science_image`.

**Requirement difference for `tclean`**: unlike `mstransform`/`flagdata`/`split`, which need an
MMS to parallelize (they split work across the MMS's existing sub-MSs), `tclean(parallel=True)`
works identically on a plain MS *or* MMS — it parallelizes internally across the major/minor
cycle structure, not by sub-MS. Caveat from the CASA docs: `savemodel='modelcolumn'` combined
with `parallel=True` triggers a race condition — only `savemodel='virtual'` or `'none'` are safe.

**How `tclean(parallel=True)` actually splits work — cube vs continuum (from CASA's
synthesis-imaging docs, "Imager Parallelization")**: this distinction matters a lot for how well
it scales, and explains why Memo 13 (below) found native MPI `tclean` limited to ~8-way in
practice.

- **Cube imaging** (`specmode='cube'`): "Parallelization for cube imaging can be naturally done
  by partitioning data and image planes by frequency for **both major and minor cycles**." Each
  MPI server owns an independent channel range end-to-end — gridding *and* deconvolution — with
  no cross-server synchronization needed until the final cube assembly. This is close to
  embarrassingly parallel, similar in spirit to Memo 13's per-channel SLURM-array approach, just
  implemented via MPI ranks inside one `tclean` call instead of independent SLURM jobs.
- **Continuum imaging** (`specmode='mfs'`): "Parallelization for continuum imaging is done
  **only for the major cycle** via data partitioning." Servers grid/predict their data partition
  in parallel, but the **minor cycle (deconvolution) runs on a single process** holding the
  combined image — every major cycle requires gathering partial images to that one process,
  deconvolving, then redistributing the updated model back out. This serialization point is
  exactly why continuum/MFS-style parallel `tclean` doesn't scale past a modest rank count the
  way cube parallelization can, regardless of how many servers are available.
- Multi-Term MFS (`nterms>1`) compounds the continuum cost further: for the same data volume, it
  requires N_terms times the gridding cost and N_terms times the images held in memory
  simultaneously.

**Status on this container (idianext.sif) + Setonix**: required three fixes, none of which touch
the container image itself:
1. `mpi4py` is missing from the container's venv, and the bundled PyPI wheel statically links
   its own MPI (never talks to Slurm's PMI/PALS). Fixed by a source build against the
   container's own dynamic MPICH, installed to
   `/software/projects/pawsey1164/ssankar/containers/idianext_mpi4py` and added to `PYTHONPATH`.
2. Cray's PALS process-launcher needs to read `/var/spool/slurmd/mpi_cray_shasta/<jobid>` on the
   compute node, which isn't in the site's default `singularity` bind list. Fixed with
   `singularity exec --bind /var/spool/slurmd`.
3. `casampi`'s `MPIEnvironment` only attempts MPI init if `OMPI_COMM_WORLD_RANK` is present in
   the environment — an OpenMPI-only check that Slurm's PMI/PALS launch never satisfies. Fixed by
   passing it through from Slurm's own `$PMI_RANK` via `--env OMPI_COMM_WORLD_RANK=$PMI_RANK`.

All three are wired into `write_command()` in `processMeerKAT.py` via `CONTAINER_ENV` /
`CONTAINER_BINDS`, scoped to `idianext.sif` only. Confirmed working end-to-end: a real 9-task
`partition.py` run produced a correctly-structured 18-sub-MS MMS in 3m35s.

## 2. SLURM job-array parallelism (already in this pipeline, untouched by the above)

The `#SBATCH --array=0-{nspw-1}` mechanism for per-SPW processing. Each array element is a
completely independent SLURM job (its own directory, own resource allocation), no MPI involved.
Orthogonal to (1) — nothing about the MPI fix changes this.

## 3. CASA Memo 13's channel-array approach (not currently used by this pipeline's calibration side)

A third strategy specifically for **cube imaging**, distinct from `tclean(parallel=True)`'s MPI
mode. Instead of MPI-parallelizing a single `tclean` call across ranks, split the cube into
individual channels and run each as a fully independent, ordinary (non-MPI) `tclean(specmode=
'mfs')` SLURM array job with its own iteration control, then concatenate at the end.

**Key results from the memo** (209 GB ALMA test dataset, 7680 channels):

| Approach | Parallelism breadth | Runtime | Speedup |
|---|---|---|---|
| `tclean`-mpi (ALMA pipeline, native `parallel=True`) | 8-way MPI | 55.5 h | 1x |
| SLURM channel-array | 10x | 13.52 h | 4x |
| SLURM channel-array | 100x | 1.74 h | 32x |
| SLURM channel-array | 1000x | 0.97 h | 57x |

- Concatenation: CASA's `image.imageconcat()` ("virtual concat") took ~10s for 1000 channels,
  vs ~593s for FITS-based concat via astropy — negligible overhead either way relative to
  imaging time.
- Per-channel resource footprint is small (the memo used 1 CPU + 20GB RAM per channel), so jobs
  keep getting scheduled even on a busy cluster, unlike one big MPI job needing many cores at once.
- Numerical results were consistent with the MPI approach (~6% flux difference attributed to
  weighting/beam-fitting differences, not a parallelization artifact).
- Sub-linear scaling from 100x→1000x was attributed to cluster resource contention (only
  200-400 jobs concurrently schedulable), not a limitation of the approach itself.
- The memo explicitly cites the **IDIA imaging pipeline** as already using this exact pattern
  ("achieves scaling up to ~few 100x for image cubes by clear partitioning across image
  channels") — i.e. an IDIA-associated sibling pipeline to this one, for imaging specifically.

## Where this leaves this pipeline

- `selfcal_part1`/`quick_tclean`/`science_image` currently use strategy (1) — MPI-parallel
  `tclean` — which now works correctly on this container/cluster after the three fixes above.
  No architectural change was needed to get it working. Whether it scales well depends on
  `specmode`: `cube` parallelizes both major and minor cycles per channel (scales much better),
  while `mfs`/continuum only parallelizes the major cycle and serializes deconvolution onto one
  rank every cycle (the likely reason Memo 13 found native MPI `tclean` limited to ~8-way).
- Strategy (3) is a legitimate, more scalable alternative worth knowing about if MPI-parallel
  `tclean` turns out to scale poorly beyond a handful of ranks in practice (the memo's own
  finding for the native ALMA-pipeline MPI mode), but it's a materially different, more invasive
  redesign of the imaging step (per-channel jobs + a concatenation step), not something to adopt
  unless the now-working MPI approach proves insufficient.
- Strategy (2), the SPW array-job mechanism, is unrelated to and unaffected by any of this.
