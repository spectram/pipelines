#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""SoFiA pass for HI cube imaging ([hi_image]) -- see "Phase 6" in REFACTOR_PLAN.md. Paired
with `hi_image.py` (one call per imaging stage, mirroring `selfcal_part1.py`/
`selfcal_part2.py`'s pairing around [selfcal] stages/loop): runs a masking pass (feeding the
next stage's mask='prev') for every non-final stage, or the final source-finding pass for
the last stage, then advances '[hi_image] stage'/'combo' -- the equivalent of
`selfcal_part2.py`'s `loop += 1`.

*Not* the same SoFiA usage as `aux_scripts/run_sofia.py` (continuum-subtraction masking) --
don't conflate them. Shares its template-copy/patch-keys/shell-out mechanism with
`cont_sofia.py` via `sofia_engine.py`."""

#This script runs in the SoFiA-only container (aux_scripts/run_sofia.py's precedent) --
#no CASA (casatasks/casatools/casampi) is available here. hi_image.py exports every
#non-final stage's image to FITS itself (on the CASA side) before this runs; the final
#stage's export similarly comes from image_engine.finalize_stage(), also on the CASA side.

import os

import config_parser
from config_parser import validate_args as va
import bookkeeping
import image_stages
import sofia_engine
import fincubes_postprocess
import processMeerKAT

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)


def main(args, taskvals):

    stages = image_stages.parse_stages(taskvals['hi_image']['stages'])
    combo = va(taskvals, 'hi_image', 'combo', int, default=0)
    stage = va(taskvals, 'hi_image', 'stage', int, default=0)

    combo_dir = 'hi_combo{0}'.format(combo)
    imagename_fn = lambda s: os.path.join(combo_dir, 'stage{0}'.format(s))
    imagename = imagename_fn(stage)

    final = image_stages.is_final(stages, stage)

    if final:
        input_fits = va(taskvals, 'hi_image', 'final_export', str)
    else:
        input_fits = imagename + '.fits'

    #output.directory + output.filename together determine where SoFiA writes the mask --
    #directory handles the 'hi_combo<N>/' prefix, filename is the basename only (matching
    #image_stages.resolve_mask()'s '<imagename_fn(stage)>_mask.fits' expectation, which
    #already includes that same prefix).
    output_dir = combo_dir
    mask_basename = 'stage{0}'.format(stage)
    #SoFiA parameter overrides distinguishing this pass from the other (S+C kernels,
    #reliability threshold, which output products get written) -- see default_config.txt's
    #'sofia_mask_params'/'sofia_final_params' comment. Dict-valued, so read directly rather
    #than via config_parser.validate_args() (str/int/float/bool only).
    if final:
        #hi_image.py already ran fincubes_postprocess.py's full data-processing step (median
        #common beam, then frequency->optical velocity, CASA side) on input_fits before this
        #job could even start -- that's the whole reason this final pass is gated to run only
        #after that step: BMAJ is only well-defined once the per-plane beams are collapsed,
        #and it's what the spatial kernel estimate below is based on. SoFiA itself therefore
        #sees the already velocity-converted cube, and the PB cube (also already
        #beam-collapsed/velocity-converted) is passed through as its gain input.
        overrides = dict(taskvals['hi_image'].get('sofia_final_params', {}))
        overrides['scfind.kernelsXY'] = fincubes_postprocess.estimate_spatial_kernels(input_fits)
        pb_fits = va(taskvals, 'hi_image', 'final_export_pb', str, default='')
        if pb_fits != '':
            overrides['input.gain'] = pb_fits
    else:
        overrides = taskvals['hi_image'].get('sofia_mask_params', {})
    sofia_engine.run_pass(processMeerKAT.SCRIPT_DIR, output_dir, final, input_fits, output_dir, mask_basename, overrides=overrides)

    if final:
        combo += 1
        stage = 0
    else:
        stage += 1

    config_parser.overwrite_config(args['config'], conf_dict={'combo': combo, 'stage': stage}, conf_sec='hi_image', sec_comment='# Internal variables for pipeline execution')


if __name__ == '__main__':

    bookkeeping.run_script(main)
