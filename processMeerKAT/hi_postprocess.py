#!/usr/bin/env python3
#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Standalone post-processing for an [hi_image] combo whose final-stage image was created
outside the normal `hi_image.py` flow -- e.g. a restore-only run (`niter=0` with the existing
residual/model, to stop cleaning without walltime-killing it), which writes `stage<N>.image` and
then crashes at MPI teardown before `hi_image.py`'s own export step. Resubmitting `hi_image.py`
for the export instead would skip `tclean` (the image exists) and hang at exit (see CLAUDE.md), so
this runs the same steps locally, once:

    [imrebin, if '[hi_image] rebin'] -> [PB-correction, if 'pb_correct'] -> exportfits
        -> median common beam -> frequency -> optical velocity

using the pipeline's own functions (`image_engine.finalize_stage()`, `fincubes_postprocess`), so
the product matches what `hi_image.py` would have written. It reads the `rebin`/`rebin_factor`/
`pb_correct` flags from the config to decide which cube is produced ('stage<N>.image_rebin.im.fits'
when rebinning, 'stage<N>.image.fits' otherwise), then points '[hi_image] final_export' in the
config at it so `hi_sofia.py`'s final pass can be run afterwards (submit `hi_sofia.sbatch`).

Safe to re-run: an existing export is kept, and the beam/velocity steps are skipped for a cube
that already has them.

Needs CASA, so run it inside the pipeline container from the run directory, e.g.:

    srun -n1 -c32 --mem=56GB --time=120 --account=<acct> --partition=work \\
        singularity exec <idianext.sif> /opt/venv/bin/python3 \\
        <pipeline>/processMeerKAT/hi_postprocess.py --config .config.tmp --combo hi_combo_r1

'--combo' is needed when the config's own '[hi_image] combo' has already advanced past the last
entry (the final `hi_sofia` does that). '--dry-run' prints what would be done without CASA."""

import argparse
import os
import sys

import config_parser
from config_parser import validate_args as va
import image_stages

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger('hi_postprocess')


def plan(taskvals, combo_spec=None):

    """What to do for one combo, from its config -- no CASA needed.

    Arguments:
    ----------
    taskvals : dict
        Parsed config (`config_parser.parse_config()`).
    combo_spec : str, optional
        Combo index or output directory name; the config's own 'combo' if omitted and valid.

    Returns:
    --------
    plan : dict
        'combo', 'combo_dir', 'image' (the final stage's CASA image), 'export_dir', 'rebin',
        'rebin_factor', 'pb_correct', 'pbthreshold', 'pbband', 'export' (expected FITS path)."""

    stages = image_stages.parse_stages(taskvals['hi_image']['stages'])
    hi_combos = taskvals['hi_image']['hi_combos']
    combo = image_stages.resolve_combo(hi_combos, combo_spec, va(taskvals, 'hi_image', 'combo', int, default=0))
    combo_dir = image_stages.combo_dirnames(hi_combos)[combo]

    rebin = va(taskvals, 'hi_image', 'rebin', bool, default=False)
    return {'combo': combo, 'combo_dir': combo_dir,
            'image': os.path.join(combo_dir, 'stage{0}.image'.format(len(stages) - 1)),
            'export_dir': os.path.join(combo_dir, 'fincubes'),
            'rebin': rebin, 'rebin_factor': taskvals['hi_image'].get('rebin_factor', [2, 2, 1]),
            'pb_correct': va(taskvals, 'hi_image', 'pb_correct', bool, default=False),
            'pbthreshold': va(taskvals, 'hi_image', 'pbthreshold', float, default=0),
            'pbband': va(taskvals, 'hi_image', 'pbband', str, default='LBand'),
            'export': image_stages.final_export_path(combo_dir, stages, rebin)}


def _spectral_axis_is_velocity(fitsfile):

    """Whether the cube has already been converted (no FREQ axis left) -- `freq_to_optical_velocity()`
    raises on a cube with none, so re-running has to skip it."""

    from astropy.io import fits
    hdr = fits.getheader(fitsfile)
    return not any(str(hdr.get('CTYPE{0}'.format(i), '')).upper().startswith('FREQ') for i in range(1, hdr['NAXIS'] + 1))


def _postprocess_cube(fitsfile):

    import fincubes_postprocess
    fincubes_postprocess.add_median_beam(fitsfile)   #a no-op once the BEAMS table is gone
    if _spectral_axis_is_velocity(fitsfile):
        logger.info("'{0}' is already on a velocity axis -- leaving it as-is.".format(fitsfile))
    else:
        fincubes_postprocess.freq_to_optical_velocity(fitsfile)


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-C', '--config', default='.config.tmp', help='Runtime config to read (and, unless --no-config-update, to point final_export in). [default: .config.tmp]')
    parser.add_argument('--combo', default=None, help="Combo index or output directory name (e.g. 'hi_combo_r1'). [default: the config's own combo, if valid]")
    parser.add_argument('--no-config-update', action='store_true', help="Don't set '[hi_image] final_export' in the config.")
    parser.add_argument('--dry-run', action='store_true', help='Print the plan and exit; needs no CASA.')
    args = parser.parse_args(argv)

    logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

    taskvals, _ = config_parser.parse_config(args.config)
    try:
        p = plan(taskvals, args.combo)
    except ValueError as err:
        logger.error(str(err))
        return 1

    logger.info("Combo {0} ('{1}'): '{2}' -> {3}rebin {4} -> '{5}'.".format(
        p['combo'], p['combo_dir'], p['image'], '' if p['rebin'] else 'no ', p['rebin_factor'] if p['rebin'] else '', p['export']))
    if not os.path.exists(p['image']):
        logger.error("'{0}' doesn't exist -- create the final stage's image first (a restore-only run: stage 'niter' 0 with the existing residual/model), from the run directory.".format(p['image']))
        return 1
    if args.dry_run:
        return 0

    import image_engine   #CASA
    exported, pb_exported = image_engine.finalize_stage(p['image'], p['export_dir'], rebin=p['rebin'],
        rebin_factor=p['rebin_factor'], pb_correct=p['pb_correct'], pbthreshold=p['pbthreshold'], pbband=p['pbband'])
    logger.info("Exported '{0}'.".format(exported))

    _postprocess_cube(exported)
    if pb_exported != '':
        _postprocess_cube(pb_exported)

    if not args.no_config_update:
        config_parser.overwrite_config(args.config, conf_dict={'final_export': "'{0}'".format(exported),
            'final_export_pb': "'{0}'".format(pb_exported)}, conf_sec='hi_image', sec_comment='# Internal variables for pipeline execution')
        logger.info("Set [hi_image] final_export = '{0}' in '{1}'.".format(exported, args.config))
    return 0


if __name__ == '__main__':
    sys.exit(main())
