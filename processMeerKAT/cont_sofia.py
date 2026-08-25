#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""SoFiA pass for continuum imaging ([cont_image]) -- see "Phase 6" in REFACTOR_PLAN.md.
Paired with `science_image.py` (one call per imaging stage), the continuum-imaging
equivalent of `hi_sofia.py` (no combo axis -- see `image_stages.py`/`sofia_engine.py`).

*Not* the same SoFiA usage as `aux_scripts/run_sofia.py` (continuum-subtraction masking) --
don't conflate them."""

#This script runs in the SoFiA-only container (aux_scripts/run_sofia.py's precedent) --
#no CASA (casatasks/casatools/casampi) is available here. science_image.py exports every
#non-final stage's image to FITS itself (on the CASA side) before this runs; the final
#stage's export similarly comes from image_engine.finalize_stage(), also on the CASA side.

import os

import config_parser
from config_parser import validate_args as va
import bookkeeping
import image_stages
import sofia_engine
import processMeerKAT

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)


def main(args, taskvals):

    stages = image_stages.parse_stages(taskvals['cont_image']['stages'])
    stage = va(taskvals, 'cont_image', 'stage', int, default=0)

    combo_dir = 'cont_image'
    imagename_fn = lambda s: os.path.join(combo_dir, 'stage{0}'.format(s))
    imagename = imagename_fn(stage)

    final = image_stages.is_final(stages, stage)

    if final:
        input_fits = va(taskvals, 'cont_image', 'final_export', str)
    else:
        input_fits = imagename + '.fits'

    output_dir = combo_dir
    mask_basename = 'stage{0}'.format(stage)
    #SoFiA parameter overrides distinguishing this pass from the other (S+C kernels,
    #reliability threshold, which output products get written) -- see default_config.txt's
    #'sofia_mask_params'/'sofia_final_params' comment. Dict-valued, so read directly rather
    #than via config_parser.validate_args() (str/int/float/bool only).
    if final:
        overrides = taskvals['cont_image'].get('sofia_final_params', {})
    else:
        overrides = taskvals['cont_image'].get('sofia_mask_params', {})
    sofia_engine.run_pass(processMeerKAT.SCRIPT_DIR, output_dir, final, input_fits, output_dir, mask_basename, overrides=overrides)

    stage = 0 if final else stage + 1
    config_parser.overwrite_config(args['config'], conf_dict={'stage': stage}, conf_sec='cont_image', sec_comment='# Internal variables for pipeline execution')


if __name__ == '__main__':

    bookkeeping.run_script(main)
