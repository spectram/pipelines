#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""HI cube imaging entry point ([hi_image]) -- see "Phase 6" in REFACTOR_PLAN.md. Thin
wrapper around `image_engine.py`'s shared tclean/PB-correction/export logic and
`image_stages.py`'s stage-list resolution; paired with `hi_sofia.py` (which runs the
masking/final SoFiA passes and advances the [hi_image] combo/stage state) the same way
`selfcal_part1.py`/`selfcal_part2.py` are paired around [selfcal] stages/loop.

Images multiple robust/uvtaper weighting combinations ([hi_image] hi_combos, one dict per
combination) end-to-end and independently -- no mask-sharing across combos, see
REFACTOR_PLAN.md's Phase 6 addendum. Each combo gets its own 'hi_combo<N>/' output
directory."""

import os
import sys

import config_parser
from config_parser import validate_args as va
import bookkeeping
import image_stages
import image_engine
import processMeerKAT

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

    vis = bookkeeping.get_hi_contsub_vis(args['config'])
    if vis == '':
        logger.error("'[run] hi_contsub_vis' is empty -- uvcontsub.py (gated on -H/--contsub) must run before hi_image.py. Check that '-H' or '--contsub' was set during '-B'.")
        sys.exit(1)

    try:
        #'stages'/'hi_combos'/'imsize'/'scales' are lists -- config_parser.validate_args()
        #only supports str/int/float/bool, so read these directly (matching
        #bookkeeping.get_selfcal_params()'s equivalent direct '[selfcal] stages' read).
        stages = image_stages.parse_stages(taskvals['hi_image']['stages'])
    except ValueError as err:
        logger.error("Invalid 'stages' in '{0}': {1}".format(args['config'], err))
        sys.exit(1)

    hi_combos = taskvals['hi_image']['hi_combos']
    combo = va(taskvals, 'hi_image', 'combo', int, default=0)
    stage = va(taskvals, 'hi_image', 'stage', int, default=0)

    if combo >= len(hi_combos):
        logger.error("'[hi_image] combo'={0} is out of range for {1} configured 'hi_combos'.".format(combo, len(hi_combos)))
        sys.exit(1)
    if not isinstance(hi_combos[combo], dict) or 'robust' not in hi_combos[combo] or 'uvtaper' not in hi_combos[combo]:
        logger.error("'[hi_image] hi_combos'[{0}] must be a dict with 'robust' and 'uvtaper' keys, e.g. {{'robust': -0.5, 'uvtaper': ''}}. Got: {1!r}".format(combo, hi_combos[combo]))
        sys.exit(1)

    robust = hi_combos[combo]['robust']
    uvtaper = hi_combos[combo]['uvtaper']

    imsize = taskvals['hi_image']['imsize']
    cell = va(taskvals, 'hi_image', 'cell', str)
    scales = taskvals['hi_image']['scales']
    gridder = va(taskvals, 'hi_image', 'gridder', str)
    wprojplanes = va(taskvals, 'hi_image', 'wprojplanes', int)
    deconvolver = va(taskvals, 'hi_image', 'deconvolver', str)
    weighting = va(taskvals, 'hi_image', 'weighting', str)
    restfreq = va(taskvals, 'cont_image', 'restfreq', str)
    imspw = va(taskvals, 'cont_image', 'imspw', str)

    logger.info('Imaging combo {0}/{1} (robust={2}, uvtaper={3!r}), stage {4}/{5}.'.format(
        combo, len(hi_combos)-1, robust, uvtaper, stage, len(stages)-1))

    combo_dir = 'hi_combo{0}'.format(combo)
    os.makedirs(combo_dir, exist_ok=True)
    imagename_fn = lambda s: os.path.join(combo_dir, 'stage{0}'.format(s))
    imagename = imagename_fn(stage)

    mask = image_stages.resolve_mask(stages, stage, imagename_fn)

    outimage = image_engine.run_stage(vis=vis, imagename=imagename, mask=mask,
        niter=stages[stage].niter, threshold=stages[stage].threshold,
        imsize=imsize, cell=cell, robust=robust, uvtaper=uvtaper, scales=scales,
        gridder=gridder, wprojplanes=wprojplanes, deconvolver=deconvolver,
        weighting=weighting, specmode='cube', restfreq=restfreq, spw=imspw,
        nterms=1, stokes='I', restoringbeam='')

    if image_stages.is_final(stages, stage):
        rebin = va(taskvals, 'hi_image', 'rebin', bool, default=False)
        rebin_factor = taskvals['hi_image'].get('rebin_factor', [2, 2, 1])
        pb_correct = va(taskvals, 'hi_image', 'pb_correct', bool, default=False)
        pbthreshold = va(taskvals, 'hi_image', 'pbthreshold', float, default=0)
        pbband = va(taskvals, 'hi_image', 'pbband', str, default='LBand')

        export_dir = os.path.join(combo_dir, 'fincubes')
        exported = image_engine.finalize_stage(outimage, export_dir, rebin=rebin, rebin_factor=rebin_factor,
            pb_correct=pb_correct, pbthreshold=pbthreshold, pbband=pbband)

        #hi_sofia.py's final pass reads this to know what to source-find on.
        config_parser.overwrite_config(args['config'], conf_dict={'final_export': "'{0}'".format(exported)}, conf_sec='hi_image', sec_comment='# Internal variables for pipeline execution')
    else:
        #hi_sofia.py's masking pass needs a FITS input, but runs in the SoFiA-only
        #container (no CASA) -- export here, on the CASA side, matching
        #selfcal_part2.py's pybdsf()/aux_scripts/run_sofia.py's existing precedent.
        fitsimage = imagename + '.fits'
        if not os.path.exists(fitsimage):
            exportfits(imagename=outimage, fitsimage=fitsimage, overwrite=True, dropdeg=True, dropstokes=True)


if __name__ == '__main__':

    bookkeeping.run_script(main, logfile)
