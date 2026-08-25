#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Continuum imaging entry point ([cont_image]) -- see "Phase 6" in REFACTOR_PLAN.md. Thin
wrapper around `image_engine.py`'s shared tclean/PB-correction/export logic and
`image_stages.py`'s stage-list resolution, matching `hi_image.py`'s pattern exactly (a
stage-list-driven, SoFiA-masked chain rather than the single fixed tclean call this script
used to make); paired with `cont_sofia.py` the same way `hi_image.py` is paired with
`hi_sofia.py`. Kept as this filename (and `-I`/`pipeline_role='science_image'`) for
continuity -- too embedded elsewhere to rename."""

import os
import sys

import config_parser
from config_parser import validate_args as va
import bookkeeping
import image_stages
import image_engine

from casatasks import exportfits, casalog
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))
import casampi

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)


def main(args, taskvals):

    vis = va(taskvals, 'cont_image', 'vis', str, default='')
    if vis == '':
        vis = va(taskvals, 'data', 'vis', str)

    try:
        #'stages'/'imsize'/'scales' are lists -- config_parser.validate_args() only
        #supports str/int/float/bool, so read these directly.
        stages = image_stages.parse_stages(taskvals['cont_image']['stages'])
    except ValueError as err:
        logger.error("Invalid 'stages' in '{0}': {1}".format(args['config'], err))
        sys.exit(1)

    stage = va(taskvals, 'cont_image', 'stage', int, default=0)

    imsize = taskvals['cont_image']['imsize']
    cell = va(taskvals, 'cont_image', 'cell', str)
    robust = va(taskvals, 'cont_image', 'robust', float)
    uvtaper = va(taskvals, 'cont_image', 'uvtaper', str)
    scales = taskvals['cont_image']['scales']
    gridder = va(taskvals, 'cont_image', 'gridder', str)
    wprojplanes = va(taskvals, 'cont_image', 'wprojplanes', int)
    deconvolver = va(taskvals, 'cont_image', 'deconvolver', str)
    weighting = va(taskvals, 'cont_image', 'weighting', str)
    nterms = va(taskvals, 'cont_image', 'nterms', int)
    specmode = va(taskvals, 'cont_image', 'specmode', str)
    restfreq = va(taskvals, 'cont_image', 'restfreq', str)
    restoringbeam = va(taskvals, 'cont_image', 'restoringbeam', str)
    stokes = va(taskvals, 'cont_image', 'stokes', str)
    outlierfile = va(taskvals, 'cont_image', 'outlierfile', str)
    if os.path.exists(outlierfile) and open(outlierfile).read() == '':
        outlierfile = ''

    logger.info('Continuum imaging stage {0}/{1}.'.format(stage, len(stages)-1))

    combo_dir = 'cont_image'
    os.makedirs(combo_dir, exist_ok=True)
    imagename_fn = lambda s: os.path.join(combo_dir, 'stage{0}'.format(s))
    imagename = imagename_fn(stage)

    mask = image_stages.resolve_mask(stages, stage, imagename_fn)

    outimage = image_engine.run_stage(vis=vis, imagename=imagename, mask=mask,
        niter=stages[stage].niter, threshold=stages[stage].threshold,
        imsize=imsize, cell=cell, robust=robust, uvtaper=uvtaper, scales=scales,
        gridder=gridder, wprojplanes=wprojplanes, deconvolver=deconvolver,
        weighting=weighting, specmode=specmode, restfreq=restfreq, spw='',
        nterms=nterms, stokes=stokes, restoringbeam=restoringbeam, outlierfile=outlierfile)

    if image_stages.is_final(stages, stage):
        rebin = va(taskvals, 'cont_image', 'rebin', bool, default=False)
        rebin_factor = taskvals['cont_image'].get('rebin_factor', [2, 2, 1])
        pb_correct = va(taskvals, 'cont_image', 'pb_correct', bool, default=False)
        pbthreshold = va(taskvals, 'cont_image', 'pbthreshold', float, default=0)
        pbband = va(taskvals, 'cont_image', 'pbband', str, default='LBand')

        export_dir = os.path.join(combo_dir, 'fincubes')
        exported = image_engine.finalize_stage(outimage, export_dir, rebin=rebin, rebin_factor=rebin_factor,
            pb_correct=pb_correct, pbthreshold=pbthreshold, pbband=pbband)

        #cont_sofia.py's final pass reads this to know what to source-find on.
        config_parser.overwrite_config(args['config'], conf_dict={'final_export': "'{0}'".format(exported)}, conf_sec='cont_image', sec_comment='# Internal variables for pipeline execution')
    else:
        #cont_sofia.py's masking pass needs a FITS input, but runs in the SoFiA-only
        #container (no CASA) -- export here, on the CASA side.
        fitsimage = imagename + '.fits'
        if not os.path.exists(fitsimage):
            exportfits(imagename=outimage, fitsimage=fitsimage, overwrite=True, dropdeg=True, dropstokes=True)


if __name__ == '__main__':

    bookkeeping.run_script(main, logfile)
