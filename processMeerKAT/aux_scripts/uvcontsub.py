#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import config_parser
from config_parser import validate_args as va
import bookkeeping

import os,sys
import casampi
from casatasks import uvcontsub,split,casalog
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

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
    fitspw = va(taskvals, "image", "fitspw", str)
    fitorder = va(taskvals, "image", "fitorder", int)

    outputvis,continuum_vis = do_uvcontsub(visname,fitspw,fitorder)

    #Unlike the old uvcontsub_old-based version, [data] vis is left untouched -- science_image.py
    #(continuum) must keep imaging the pre-contsub data, not silently pick up contsub'd data just
    #because this script happened to run first in postcal_scripts. Phase 6's HI scripts read
    #'outputvis' explicitly via bookkeeping.get_hi_contsub_vis().
    config_parser.overwrite_config(args['config'], conf_dict={'hi_contsub_vis' : "'{0}'".format(outputvis)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
    config_parser.overwrite_config(args['config'], conf_dict={'continuum_vis' : "'{0}'".format(continuum_vis)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

if __name__ == '__main__':

    bookkeeping.run_script(main)
