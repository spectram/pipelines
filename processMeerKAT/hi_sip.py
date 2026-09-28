#!/usr/bin/env python3
#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Manual sofia-image-pipeline (SIP) re-run for an [hi_image] combo, to add the survey (optical)
overlay when the pipeline's own SIP step couldn't.

`hi_sofia.py` runs SIP after the final SoFiA pass, but on compute nodes astroquery's survey-list
request can block forever, so `sip_postprocess.run_sip()` falls back to offline mode (`-s none`) and
the figures come out without the DSS2 overlay. The same request works from a login node. This
finds the combo's final catalogue and cube, reports which sources lack an overlay, and (unless
'--check') re-runs SIP there *requiring* the survey -- if SkyView still can't be reached it stops
rather than quietly producing offline figures again. Sources that already have an overlay are left
alone unless '--force'.

Run it on a login node, from any directory, with a Python that has SIP's dependencies (astropy,
matplotlib, astroquery, pvextractor, Pillow) -- e.g. the SoFiA container, or an environment with
`pip install sofia-image-pipeline`. The `sip` package itself is found through `processMeerKAT.SIP_PATH`
(or '--sip-path'). ImageMagick isn't needed: without it the combined per-source figure is built
with Pillow (`sip_postprocess.combine_figures_pillow()`).

    hi_sip.py --config <run dir>/.config.tmp --combo hi_combo_r1 [--check] [--force]

CASA-free."""

import argparse
import os
import re
import sys

import config_parser
from config_parser import validate_args as va
import image_stages
import sip_postprocess

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger('hi_sip')


def find_inputs(taskvals, combo_spec=None, catalog=None, cube=None, workdir='.'):

    """Locate the final catalogue and cube for one combo.

    Arguments:
    ----------
    taskvals : dict
        Parsed config.
    combo_spec : str, optional
        Combo index or output directory name; the config's own 'combo' if omitted and valid.
    catalog, cube : str, optional
        Explicit paths, overriding what's derived from the config.
    workdir : str, optional
        Run directory the config's relative paths are relative to.

    Returns:
    --------
    catalog, cube, figdir, base : str
        Catalogue path, the cube SoFiA ran on, SIP's figure directory, and the catalogue basename."""

    stages = image_stages.parse_stages(taskvals['hi_image']['stages'])
    hi_combos = taskvals['hi_image']['hi_combos']
    combo = image_stages.resolve_combo(hi_combos, combo_spec, va(taskvals, 'hi_image', 'combo', int, default=0))
    combo_dir = image_stages.combo_dirnames(hi_combos)[combo]
    export_dir = os.path.join(workdir, combo_dir, 'fincubes')

    base = 'stage{0}'.format(len(stages) - 1)
    catalog = catalog or os.path.join(export_dir, base + '_cat.xml')
    if catalog.endswith('_cat.xml'):
        base = re.sub(r'_cat\.xml$', '', os.path.basename(catalog))

    if not cube:
        rebin = va(taskvals, 'hi_image', 'rebin', bool, default=False)
        cube = os.path.join(workdir, image_stages.final_export_path(combo_dir, stages, rebin))
    return catalog, cube, os.path.join(os.path.dirname(os.path.abspath(catalog)), base + '_figures'), base


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-C', '--config', default='.config.tmp', help='Runtime config. [default: .config.tmp]')
    parser.add_argument('--combo', default=None, help="Combo index or output directory name (e.g. 'hi_combo_r1'). [default: the config's own combo, if valid]")
    parser.add_argument('--catalog', default=None, help='SoFiA catalogue (<base>_cat.xml), instead of the combo default.')
    parser.add_argument('--cube', default=None, help='Cube SoFiA ran on (SIP -o), instead of the combo default.')
    parser.add_argument('--surveys', default='', help="SIP -s value (e.g. 'DSS2 Blue'); empty leaves SIP's own default. 'none' runs offline.")
    parser.add_argument('--sip-path', default=None, help='Directory holding the sip package. [default: processMeerKAT.SIP_PATH]')
    parser.add_argument('--check', action='store_true', help='Only report which sources lack an overlay; exit 1 if any.')
    parser.add_argument('--force', action='store_true', help='Re-run even if every source already has an overlay.')
    args = parser.parse_args(argv)

    logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

    taskvals, _ = config_parser.parse_config(args.config)
    workdir = os.path.dirname(os.path.abspath(args.config))
    try:
        catalog, cube, figdir, base = find_inputs(taskvals, args.combo, args.catalog, args.cube, workdir)
    except ValueError as err:
        logger.error(str(err))
        return 1

    for path, what in ((catalog, 'catalogue'), (cube, 'cube')):
        if not os.path.exists(path):
            logger.error("The {0} '{1}' doesn't exist -- has the final hi_sofia pass run?".format(what, path))
            return 1

    missing = sip_postprocess.missing_survey_overlays(figdir, base)
    if args.check:
        logger.info('Sources without a survey overlay: {0}.'.format(missing if missing else 'none'))
        return 1 if missing else 0
    if not missing and os.path.isdir(figdir) and not args.force:
        logger.info("Every source in '{0}' already has a survey overlay -- nothing to do (use --force to redo).".format(figdir))
        return 0

    sip_path = args.sip_path
    if sip_path is None:
        try:
            import processMeerKAT
            sip_path = processMeerKAT.SIP_PATH
        except Exception:
            sip_path = ''

    logger.info("Running SIP on '{0}' with the survey overlay required.".format(catalog))
    ok = sip_postprocess.run_sip(catalog, cube, sip_path=sip_path, surveys=args.surveys, require_survey=args.surveys.lower() != 'none')
    if not ok:
        logger.error('SIP did not complete with the survey overlay.')
        return 2

    missing = sip_postprocess.missing_survey_overlays(figdir, base)
    if missing and args.surveys.lower() != 'none':
        logger.error('SIP finished but sources {0} still have no survey overlay (no image covering them?).'.format(missing))
        return 3
    logger.info("Done: figures in '{0}'.".format(figdir))
    return 0


if __name__ == '__main__':
    sys.exit(main())
