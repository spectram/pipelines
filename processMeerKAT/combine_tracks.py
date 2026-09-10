#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Combine multiple tracks' per-track output into one MS for subsequent imaging -- see
"Phase 10" in REFACTOR_PLAN.md. Two halves:

- discover_tracks()/resolve_track_vis()/write_combined_config() are pure config/path logic,
  no CASA needed, unit-testable standalone with plain python3. Called from processMeerKAT.py's
  '--combine' at prep time (on the login node, like '-B' -- not inside a SLURM job).
- main() is the actual virtualconcat call, CASA-dependent, run inside the sbatch
  processMeerKAT.py's '--combine' generates -- reads only its own already-prepared config (the
  resolved 'source_vis' list write_combined_config() persists into '[combine]'), never reads
  another directory's config at job-run time, so there's no race with a source track's own
  state changing between discovery and the combine job actually running.

Ported from HI-dev's m2-image-scripts/combine_tracks.py prototype (hardcoded to exactly 2
tracks, Ilifu-specific paths/account/container) -- generalized to N tracks, Pawsey-native, and
config-driven rather than a bare hardcoded script. Keeps the prototype's own design choice of
combining *after* contsub for the HI case (each track keeps its own independently-derived
continuum fit, rather than forcing one shared fit across tracks with different UV
coverage/flagging) -- porting the parameter choice, not just the mechanism, the same principle
Phase 6 used for hi_image.py.

Deliberately standalone -- not part of the '-B'/'-R' DAG (no per-run-directory scoping exists
for a tool that reads across multiple independent run directories) and not registered in
script_registry.py (which only describes scripts that flow through write_sbatch()/
write_command() as part of one run's generated DAG, which this never does)."""

import os
import re
import sys
import ast

import config_parser
import bookkeeping

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)


#Matches a bare production-track directory name ('P1', 'P2', 'P23', ...). Deliberately excludes
#'_test'-suffixed directories (e.g. 'P1_test') -- those are explicit test runs, not production
#tracks to combine by default; pass an explicit 'tracks' list to include one anyway.
TRACK_DIR_PATTERN = re.compile(r'^P\d+$')


def discover_tracks(wdir, pattern=TRACK_DIR_PATTERN):

    """Find sibling per-track run directories under 'wdir' -- any subdirectory whose basename
    matches 'pattern'. Returns an absolute-path list, sorted for deterministic ordering.

    Arguments:
    ----------
    wdir : str
        Parent directory containing the per-track run directories (e.g. the directory holding
        'P1'/'P2'/'P3'/'P4').
    pattern : re.Pattern, optional
        Compiled regex a directory's basename must match to be treated as a track.

    Returns:
    --------
    tracks : list
        Absolute paths to the discovered track directories, sorted by name."""

    if not os.path.isdir(wdir):
        raise ValueError("'{0}' is not a directory.".format(wdir))

    tracks = []
    for name in sorted(os.listdir(wdir)):
        full = os.path.join(wdir, name)
        if os.path.isdir(full) and pattern.match(name):
            tracks.append(os.path.abspath(full))

    if not tracks:
        raise ValueError("No track directories matching '{0}' found under '{1}'.".format(pattern.pattern, wdir))

    return tracks


def resolve_track_vis(track_dir, hi_image, config_name='.config.tmp'):

    """Return the correct source visibility for one track, read from that track's own runtime
    config -- '[run] hi_contsub_vis' (HI-imaging combine mode) or '[run] post_selfcal_vis'
    (continuum-imaging combine mode -- needs Phase 10b's science_image.py fix to be trustworthy;
    see REFACTOR_PLAN.md). Reads '.config.tmp', not 'myconfig.txt' -- these keys are written by
    uvcontsub.py/uvsub.py via config_parser.overwrite_config(args['config'], ...), and
    args['config'] at runtime is always '.config.tmp', never 'myconfig.txt' (see this repo's own
    resume-recipe documentation in CLAUDE.md for the same '.config.tmp' vs 'myconfig.txt'
    distinction).

    Arguments:
    ----------
    track_dir : str
        Path to one track's own run directory.
    hi_image : bool
        Resolve the HI-imaging source ('.contsub') if True, else the continuum-imaging source
        ('.post_selfcal').
    config_name : str, optional
        Runtime config filename within 'track_dir'.

    Returns:
    --------
    vis : str
        Absolute path to that track's source visibility.

    Raises:
    -------
    ValueError
        If the track hasn't reached the required pipeline stage yet."""

    config_path = os.path.join(track_dir, config_name)
    if not os.path.exists(config_path):
        raise ValueError("'{0}' has no {1} -- has it been -R'd/run yet?".format(track_dir, config_name))

    if hi_image:
        vis = bookkeeping.get_hi_contsub_vis(config_path)
        key, script = 'hi_contsub_vis', 'uvcontsub.py'
    else:
        vis = bookkeeping.get_post_selfcal_vis(config_path)
        key, script = 'post_selfcal_vis', 'uvsub.py'

    if not vis:
        raise ValueError("'{0}' has no [run] {1} yet -- {2} hasn't run there yet.".format(track_dir, key, script))

    #hi_contsub_vis/post_selfcal_vis are filenames relative to their own track directory.
    return os.path.abspath(os.path.join(track_dir, vis))


def write_combined_config(output_dir, tracks, source_vis, output_vis, hi_image, template_config=None):

    """Write a trimmed myconfig.txt in 'output_dir' for the combined MS -- [data]/[run]/[combine]
    always, plus a copy of [hi_image] (from 'template_config', typically one of the source
    tracks' own myconfig.txt) when 'hi_image' is True. Deliberately omits [crosscal]/[selfcal]/
    [contsub] -- that work is already baked into the per-track inputs being combined, and
    re-exposing those sections would misleadingly suggest they still need running here.

    [combine] records the resolved inputs (tracks + their absolute source_vis paths) so the
    actual combine_tracks.py compute job -- run inside a submitted sbatch, separately from this
    function -- only ever reads its own config; it never re-reads another directory's config at
    job-run time.

    Arguments:
    ----------
    output_dir : str
        Directory to write the combined config into (created if it doesn't exist).
    tracks : list
        Source track directories (as returned by discover_tracks()), recorded for provenance.
    source_vis : list
        Resolved absolute source visibility paths (as returned by resolve_track_vis(), one per
        track, same order as 'tracks').
    output_vis : str
        Filename (not path) of the combined MS to be written into 'output_dir'.
    hi_image : bool
        Combining for HI imaging (True) or continuum imaging (False)?
    template_config : str, optional
        Path to a config to copy [hi_image] from, when hi_image=True. Required in that case.

    Returns:
    --------
    output_config : str
        Path to the written config."""

    if hi_image and not template_config:
        raise ValueError("template_config is required when hi_image=True (need somewhere to copy [hi_image] from).")

    os.makedirs(output_dir, exist_ok=True)
    output_config = os.path.join(output_dir, 'myconfig.txt')

    concatvis = os.path.join(output_dir, output_vis)
    config_parser.overwrite_config(output_config, conf_dict={'vis': "'{0}'".format(concatvis)}, conf_sec='data')
    config_parser.overwrite_config(output_config,
        conf_dict={'tracks': repr(tracks), 'source_vis': repr(source_vis), 'hi_image': hi_image},
        conf_sec='combine', sec_comment='# Tracks combined into [data] vis, and how -- see combine_tracks.py')
    config_parser.overwrite_config(output_config, conf_dict={'continue': True}, conf_sec='run',
        sec_comment='# Internal variables for pipeline execution')

    if hi_image:
        template_dict, _ = config_parser.parse_config(template_config)
        hi_image_section = template_dict.get('hi_image', {})
        if not hi_image_section:
            raise ValueError("'{0}' has no [hi_image] section to copy.".format(template_config))
        #overwrite_config() just str()s whatever it's given -- template_dict's values are
        #already parsed (ast.literal_eval()'d) Python values, e.g. a plain unquoted string, so
        #they need re-quoting via repr() to round-trip back through config parsing correctly
        #(str() alone would write e.g. imspw's value as bare, unquoted text).
        config_parser.overwrite_config(output_config, conf_dict={k: repr(v) for k, v in hi_image_section.items()}, conf_sec='hi_image')

    return output_config


def main(args, taskvals):

    from casatasks import casalog, virtualconcat
    casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

    source_vis = taskvals['combine']['source_vis']
    if isinstance(source_vis, str):
        source_vis = ast.literal_eval(source_vis)
    vis = config_parser.validate_args(taskvals, 'data', 'vis', str)

    if os.path.exists(vis):
        logger.info('"{0}" already exists. Not overwriting, continuing.'.format(vis))
        return

    logger.info('Combining {0} tracks into "{1}": {2}'.format(len(source_vis), vis, source_vis))
    #keepcopy=True: CASA copies the inputs internally before concatenating, leaving the
    #originals (each track's own '[run] hi_contsub_vis'/'post_selfcal_vis') untouched -- the
    #same originals-preserved guarantee the prototype achieved via a manual copytree() +
    #keepcopy=False, in one I/O pass instead of two.
    virtualconcat(vis=source_vis, concatvis=vis, keepcopy=True)
    logger.info('Wrote "{0}".'.format(vis))


if __name__ == '__main__':

    #Not bookkeeping.run_script(): that helper unconditionally validates [crosscal] spw/nspw
    #(for its nspw>1 continue=False broadcasting to every SPW subdirectory on error) -- logic
    #that doesn't apply here, since this script's config deliberately has no [crosscal] section
    #and there's no per-SPW fanout to broadcast to. Minimal self-contained equivalent instead.
    args = config_parser.parse_args()
    taskvals, config = config_parser.parse_config(args['config'])
    continue_run = config_parser.validate_args(taskvals, 'run', 'continue', bool, default=True)
    if not continue_run:
        logger.error('Exception found in a previous step, which set "continue=False" in [run] section of "{0}". Skipping.'.format(args['config']))
        sys.exit(1)
    try:
        main(args, taskvals)
    except Exception as err:
        logger.error('Exception found in combine_tracks.py: {0}: {1}'.format(type(err), err))
        import traceback
        logger.error(traceback.format_exc())
        config_parser.overwrite_config(args['config'], conf_dict={'continue': False}, conf_sec='run',
            sec_comment='# Internal variables for pipeline execution')
        sys.exit(1)
