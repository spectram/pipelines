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
    #Forces the job onto the 'long' partition (was: 'selfcal'/'image' in script,
    #write_sbatch()).
    long_running: bool = False
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
    'selfcal_part2.py':  ScriptProperties(cpu_intensive=True, long_running=True, pipeline_role='selfcal_part2'),
    'run_sofia.py':      ScriptProperties(pipeline_role='run_sofia'),
    'uvsub.py':          ScriptProperties(pipeline_role='uvsub'),
    'uvcontsub.py':      ScriptProperties(requires_mms=True, pipeline_role='uvcontsub'),
    'science_image.py':  ScriptProperties(cpu_intensive=True, long_running=True, pipeline_role='science_image'),
}


def get_properties(script):

    """Look up a script's declared ScriptProperties by filename (basename, with or without
    a directory prefix). Unknown scripts (e.g. user-supplied custom scripts passed via -S)
    get all-default ScriptProperties(), matching today's behaviour of simply not matching
    any of the substring checks this registry replaces.

    Arguments:
    ----------
    script : str
        Script filename (e.g. 'selfcal_part1.py'), optionally with a directory prefix.

    Returns:
    --------
    properties : class ``ScriptProperties``"""

    import os
    return REGISTRY.get(os.path.basename(script), ScriptProperties())
