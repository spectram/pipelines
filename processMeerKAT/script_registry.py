#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Declared per-script properties, replacing the substring-matching-on-script-filename
pattern found throughout processMeerKAT.py (e.g. `'tclean' in script`, `'selfcal' in
script`, `'partition' in script`) with a single lookup table. Each property here
corresponds to one (or more) existing substring check; see the call site being migrated
for the exact behaviour a given property drives. Migrated one property/call-site group at
a time (see the Pawsey refactor plan), verified against tools/golden_diff.sh after each
step -- so at any point in that migration, some properties below may not yet be read by
any call site.

A script not present in REGISTRY (e.g. a user-supplied custom script passed via -S) gets
ScriptProperties()'s all-False/default-True defaults, which reproduces today's behaviour
for scripts that don't match any of the substrings this registry replaces."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScriptProperties:
    #Gets extra cpus-per-task from CPUS_PER_NODE_LIMIT (was: 'tclean'/'selfcal'/'image'/
    #'flag'/'partition' in script, write_sbatch()).
    cpu_intensive: bool = False
    #Needs the whole node's memory regardless of core count, via `#SBATCH --exclusive`
    #(was: 'selfcal_part1' in script, write_sbatch()).
    exclusive_node: bool = False
    #Is the SPW-splitting/fan-out step: gets a SLURM `--array` job when nspw>1, and is the
    #one precal script every other precal/postcal script implicitly depends on running
    #first (was: 'partition' in script, write_command()/write_sbatch()/write_master()/
    #format_args()).
    is_spw_fanout: bool = False
    #Reserved for Phase 7a (Memo-13 channel-parallel array jobs for HI cube imaging) --
    #not yet read by any call site.
    array_job_capable: bool = False
    #Needs xvfb-run (was: 'plot' in script, write_sbatch()).
    plot: bool = False
    #Invoked via legacy monolithic `casa --nologger -c ...` rather than this container's
    #own python3 (was: the `casa_script` flag threaded through write_sbatch()/
    #write_command()). NOTE: on the current Pawsey container no caller ever actually sets
    #this True today -- CONTAINER_PYTHON's modular casatasks python replaced the monolithic
    #`casa` binary entirely, so this toggle is effectively dead weight kept only for
    #Ilifu-era parity; worth reconsidering when this field's call site is migrated.
    casa_invocation: bool = True
    #Raises the open-file-descriptor ulimit before running (was: 'selfcal'/'image' in
    #script, write_sbatch()).
    long_running: bool = False
    #Forces the job onto the 'long' partition (4-day walltime cap vs. 'work''s 24h) --
    #split out from long_running rather than reusing it, since selfcal_part1/part2's ulimit
    #need and their walltime risk aren't the same thing: selfcal was routed to 'long'
    #by-default for loop 3's niter=1000000 deep clean (Phase 7b's stated 24h-cap concern),
    #but forcing every user onto a 4-day-cap, 8-node partition regardless of image size or
    #[selfcal] stages config caused real queue-priority pain even for small/quick runs (a
    #reduced-scale Phase 2 smoke test queued ~24h out on 'long' purely from priority, not
    #actual resource need) -- so selfcal no longer forces a partition at all; it uses
    #whatever [slurm] partition the user configures (Phase 7b should reintroduce an
    #opt-in-per-run escape to 'long' when a stage's niter/threshold genuinely risks exceeding
    #'work''s 24h cap, rather than the previous unconditional override). science_image.py
    #still forces 'long' unchanged.
    long_partition: bool = False
    #Needs an MMS (not just an MS) to get its own parallelism, e.g. mstransform/flagdata/
    #split-family tasks that split work across an MMS's existing sub-MSs -- as opposed to
    #tclean(parallel=True), which parallelizes over its own major/minor cycle structure and
    #works on a plain MS or MMS equally (documentation only; not yet read by any call site,
    #see CLAUDE.md).
    requires_mms: bool = False
    #Presence of this script in the configured script list forces dopol=True (was: the
    #'xy_yx_solve.py'/'xy_yx_apply.py' literal checks, format_args()).
    forces_dopol: bool = False
    #Stable identifier for this script's role in the pipeline DAG, for call sites that
    #currently match on the literal filename (e.g. write_master()'s selfcal-loop
    #replication, default_config()'s remove_scripts).
    pipeline_role: Optional[str] = None


REGISTRY = {
    'calc_refant.py':    ScriptProperties(pipeline_role='calc_refant'),
    'partition.py':      ScriptProperties(cpu_intensive=True, is_spw_fanout=True, requires_mms=True, pipeline_role='partition'),
    'validate_input.py': ScriptProperties(casa_invocation=False, pipeline_role='validate_input'),
    'flag_round_1.py':   ScriptProperties(cpu_intensive=True, requires_mms=True, pipeline_role='flag_round_1'),
    'flag_round_2.py':   ScriptProperties(cpu_intensive=True, requires_mms=True, pipeline_role='flag_round_2'),
    'setjy.py':          ScriptProperties(requires_mms=True, pipeline_role='setjy'),
    'xx_yy_solve.py':    ScriptProperties(pipeline_role='xx_yy_solve'),
    'xx_yy_apply.py':    ScriptProperties(requires_mms=True, pipeline_role='xx_yy_apply'),
    'xy_yx_solve.py':    ScriptProperties(forces_dopol=True, pipeline_role='xy_yx_solve'),
    'xy_yx_apply.py':    ScriptProperties(forces_dopol=True, requires_mms=True, pipeline_role='xy_yx_apply'),
    'split.py':          ScriptProperties(requires_mms=True, pipeline_role='split'),
    'quick_tclean.py':   ScriptProperties(cpu_intensive=True, pipeline_role='quick_tclean'),
    'concat.py':         ScriptProperties(pipeline_role='concat'),
    'plotcal_spw.py':    ScriptProperties(plot=True, pipeline_role='plotcal_spw'),
    'selfcal_part1.py':  ScriptProperties(cpu_intensive=True, exclusive_node=True, long_running=True, pipeline_role='selfcal_part1'),
    #cpu_intensive=False (not True, unlike selfcal_part1): this is single/lightly-threaded PyBDSF
    #source-finding plus simple CASA mask ops, and (for a loop with derive_cal != '', or the final
    #loop) a non-parallel (parallel=False) predict-only tclean(niter=0)+gaincal -- none of that
    #scales with core count the way selfcal_part1's real MPI-parallel deep clean does. Previously
    #cpu_intensive=True pulled this to 128 cpus-per-task (tasks=1, cpu_intensive's heuristic is
    #cpus_per_node/tasks), which in turn pulled --mem up to the full 230GB node cap via the shared
    #mem/cpu ratio -- confirmed via real profiling (HI_p1) this was ~2-18% utilized (4.96GB for the
    #derive_cal=='' skip-branch, 38-41GB for the predict+gaincal branches) -- see
    #write_sbatch()'s own selfcal_part2-specific mem floor (profiled-informed, not this heuristic).
    'selfcal_part2.py':  ScriptProperties(cpu_intensive=False, long_running=True, pipeline_role='selfcal_part2'),
    'run_sofia.py':      ScriptProperties(pipeline_role='run_sofia'),
    'uvsub.py':          ScriptProperties(pipeline_role='uvsub'),
    'uvcontsub.py':      ScriptProperties(requires_mms=True, pipeline_role='uvcontsub'),
    'science_image.py':  ScriptProperties(cpu_intensive=True, long_running=True, long_partition=True, pipeline_role='science_image'),
    #Phase 6 (Pawsey refactor): HI cube imaging + continuum imaging's own SoFiA-driven
    #stage chain -- see image_stages.py/image_engine.py/sofia_engine.py and
    #REFACTOR_PLAN.md's Phase 6 write-up.
    #long_partition=False (not True): unlike selfcal_part1's deep clean (which has no
    #configurable per-script time budget of its own), hi_image.py's walltime is entirely
    #governed by the ordinary [slurm] time value like every other script -- unconditionally
    #routing it to 'long' (8 nodes total on Setonix, vs. work's 1368) regardless of whether
    #the configured time actually needs 'long''s longer walltime cap risks a much longer
    #queue wait than the job itself takes to run for the common case (e.g. time<=24h, which
    #fits 'work' fine). long_running=True (the ulimit bump, decoupled from partition routing
    #since Phase 3/4) stays -- that's still warranted for a deep HI clean's open-file count.
    'hi_image.py':        ScriptProperties(cpu_intensive=True, long_running=True, long_partition=False, pipeline_role='hi_image'),
    'hi_sofia.py':         ScriptProperties(pipeline_role='hi_sofia'),
    'cont_sofia.py':      ScriptProperties(pipeline_role='cont_sofia'),
}


def get_properties(script):

    """Look up a script's declared ScriptProperties by filename (basename, with or without
    a directory prefix, and with either a '.py' or '.sbatch' extension -- write_master()/
    write_spw_master() operate on the generated '<script>.sbatch' filenames rather than the
    original '<script>.py'). Unknown scripts (e.g. user-supplied custom scripts passed via
    -S) get all-default ScriptProperties(), matching today's behaviour of simply not
    matching any of the substring checks this registry replaces.

    Arguments:
    ----------
    script : str
        Script filename (e.g. 'selfcal_part1.py' or 'selfcal_part1.sbatch'), optionally
        with a directory prefix.

    Returns:
    --------
    properties : class ``ScriptProperties``"""

    import os
    basename = os.path.basename(script)
    if basename.endswith('.sbatch'):
        basename = basename[:-len('.sbatch')] + '.py'
    return REGISTRY.get(basename, ScriptProperties())
