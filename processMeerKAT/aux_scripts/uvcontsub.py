#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import config_parser
from config_parser import validate_args as va
import bookkeeping
import contsub_utils

import os,sys
import casampi
from casatasks import uvcontsub,split,casalog
from casatools import msmetadata,quanta
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))
msmd = msmetadata()
qa = quanta()

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def get_fitspec(vis, taskvals):

    """Resolve uvcontsub's 'fitspec' -- an explicit [contsub] fitspw always wins (never
    overridden). Left blank, computed fresh here (not in read_ms.py at '-B' time) via a real
    msmd query against 'vis', since uvcontsub's own bespoke 'fitspec' parser needs real,
    explicit per-SPW IDs (confirmed live: it doesn't accept flagdata-style '*' wildcards) --
    and those IDs only exist on the MS after partition.py/concat.py's per-SPW fan-out and
    re-concatenation, not at '-B' time against the raw input MS's own (different) SPW
    structure. See contsub_utils.estimate_fitspec()'s docstring for the full story.

    Arguments:
    ----------
    vis : str
        Input MeasurementSet -- queried directly for its real SPW structure.
    taskvals : dict
        Parsed config.

    Returns:
    --------
    fitspec : str
        The MSSelection string to pass as uvcontsub's 'fitspec'."""

    fitspw = va(taskvals, "contsub", "fitspw", str, default='')
    if fitspw != '':
        return fitspw

    restfreq_mhz = qa.convert(va(taskvals, "contsub", "restfreq", str, default='1420.406MHz'), 'MHz')['value']
    target_velocity = va(taskvals, "contsub", "target_velocity", float)
    fitspw_vwidth = va(taskvals, "contsub", "fitspw_vwidth", float, default=800.0)

    msmd.open(vis)
    spw_ranges = [(i, msmd.chanfreqs(i)[0] / 1e6, msmd.chanfreqs(i)[-1] / 1e6) for i in range(msmd.nspw())]
    msmd.done()

    fitspec = contsub_utils.estimate_fitspec(spw_ranges, restfreq_mhz, target_velocity, fitspw_vwidth)
    logger.info("[contsub] fitspw not set -- auto-estimated fitspec '{0}' from target_velocity={1}km/s, "
        "fitspw_vwidth={2}km/s against {3}'s real SPW structure.".format(fitspec, target_velocity, fitspw_vwidth, vis))
    return fitspec

def do_uvcontsub(vis,fitspw,fitorder):

    """Continuum-subtract 'vis' with the new uvcontsub task, and materialize a standalone
    continuum-only MS from the continuum model it writes -- the new task has no direct
    'want_cont' parameter (unlike the old uvcontsub_old task this replaces), so this reproduces
    it explicitly via a follow-up split() on the model column. Idempotent (skips work whose
    output already exists), matching science_image.py's convention -- the new task errors
    outright if 'outputvis' already exists.

    Arguments:
    ----------
    vis : str
        Input MeasurementSet (relative or absolute path).
    fitspw : str
        MSSelection string of channels to fit (and exclude from the fit) as continuum.
    fitorder : int
        Order of polynomial fit to continuum.

    Returns:
    --------
    outputvis : str
        Path to the continuum-subtracted MS.
    continuum_vis : str
        Path to the standalone continuum-only MS."""

    outputvis = vis + '.contsub'
    continuum_vis = vis + '.cont'

    if not os.path.exists(outputvis):
        uvcontsub(vis=vis, outputvis=outputvis, datacolumn='corrected', fitspec=fitspw, fitorder=fitorder, writemodel=True)
    else:
        logger.info('"{0}" already exists. Not overwriting, continuing to next step.'.format(outputvis))

    if not os.path.exists(continuum_vis):
        split(vis=vis, outputvis=continuum_vis, datacolumn='model')
    else:
        logger.info('"{0}" already exists. Not overwriting, continuing to next step.'.format(continuum_vis))

    return outputvis,continuum_vis

def main(args,taskvals):

    visname = va(taskvals, "data", "vis", str)
    fitspw = get_fitspec(visname, taskvals)
    fitorder = va(taskvals, "contsub", "fitorder", int)

    #Persist the resolved fitspec into [contsub] fitspw for visibility/debugging -- matches
    #this pipeline's convention elsewhere of writing auto-derived values back into config
    #(e.g. [hi_image] imspw). A no-op when the user already set fitspw explicitly (same value
    #written back).
    config_parser.overwrite_config(args['config'], conf_dict={'fitspw' : "'{0}'".format(fitspw)}, conf_sec='contsub')

    outputvis,continuum_vis = do_uvcontsub(visname,fitspw,fitorder)

    #Unlike the old uvcontsub_old-based version, [data] vis is left untouched -- science_image.py
    #(continuum) must keep imaging the pre-contsub data, not silently pick up contsub'd data just
    #because this script happened to run first in postcal_scripts. Phase 6's HI scripts read
    #'outputvis' explicitly via bookkeeping.get_hi_contsub_vis().
    config_parser.overwrite_config(args['config'], conf_dict={'hi_contsub_vis' : "'{0}'".format(outputvis)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
    config_parser.overwrite_config(args['config'], conf_dict={'continuum_vis' : "'{0}'".format(continuum_vis)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

if __name__ == '__main__':

    bookkeeping.run_script(main)
