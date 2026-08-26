#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import sys
import os
import shutil

import config_parser
import bookkeeping
from config_parser import validate_args as va

from casatasks import *
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def do_parallel_cal(visname, fields, calfiles, referenceant, caldir,
        minbaselines, standard, calib_uvrange='', bpsmooth_kernel=0):

    #calib_uvrange (e.g. '>150m', matching MHONGOOSE/de Blok et al. 2024 sec 5.1) is applied
    #uniformly to all three solves below, by design -- unlike that paper, which derives delay
    #specifically from its "primary" calibrator, this pipeline's delay (K) solve uses
    #fields.kcorrfield = the phase calibrator (see bookkeeping.get_field_ids()), not
    #fields.bpassfield (the bandpass/flux calibrator, our closest match to their "primary").
    #The underlying rationale for the cut -- avoiding short-baseline systematics/resolved
    #calibrator structure -- applies regardless of which field is being solved, so one uniform
    #knob was chosen over trying to replicate their exact primary-vs-secondary split onto a
    #pipeline that assigns fields to calibration steps differently. Off ('') by default.
    if not os.path.isdir(caldir):
        os.makedirs(caldir)
    elif not os.path.isdir(caldir+'_round1'):
        os.rename(caldir,caldir+'_round1')
        os.makedirs(caldir)

    logger.info(" starting antenna-based delay (kcorr)\n -> %s" % calfiles.kcorrfile)
    gaincal(vis=visname, caltable = calfiles.kcorrfile, field
            = fields.kcorrfield, refant = referenceant,
            minblperant = minbaselines, solnorm = False,  gaintype = 'K',
            solint = 'inf', combine = '', uvrange = calib_uvrange, parang = False, append = False)
    bookkeeping.check_file(calfiles.kcorrfile)

    logger.info(" starting bandpass -> %s" % calfiles.bpassfile)
    bandpass(vis=visname, caltable = calfiles.bpassfile,
            field = fields.bpassfield, refant = referenceant,
            minblperant = minbaselines, solnorm = False,  solint = 'inf',
            combine = 'scan', bandtype = 'B', fillgaps = 8, uvrange = calib_uvrange,
            gaintable = calfiles.kcorrfile, gainfield = fields.kcorrfield,
            parang = False, append = False)
    bookkeeping.check_file(calfiles.bpassfile)

    #Box-car smooth the bandpass for S/N, matching MHONGOOSE (de Blok et al. 2024) -- off by
    #default (bpsmooth_kernel=0). Must run before this table is used as a gaintable below.
    bookkeeping.smooth_bandpass_caltable(calfiles.bpassfile, bpsmooth_kernel)

    logger.info(" starting gain calibration\n -> %s" % calfiles.gainfile)
    gaincal(vis=visname, caltable = calfiles.gainfile,
            field = fields.gainfields, refant = referenceant,
            minblperant = minbaselines, solnorm = False,  gaintype = 'G',
            solint = 'inf', combine = '', calmode='ap', uvrange = calib_uvrange,
            gaintable=[calfiles.kcorrfile, calfiles.bpassfile],
            gainfield=[fields.kcorrfield, fields.bpassfield],
            parang = False, append = False)
    bookkeeping.check_file(calfiles.gainfile)

    # Only run fluxscale if bootstrapping
    if len(fields.gainfields.split(',')) > 1:
        fluxscale(vis=visname, caltable=calfiles.gainfile,
                reference=[fields.fluxfield], transfer='',
                fluxtable=calfiles.fluxfile, append=False, display=False,
                listfile = os.path.join(caldir,'fluxscale_xx_yy.txt'))
        bookkeeping.check_file(calfiles.fluxfile)

def main(args,taskvals):

    visname = va(taskvals, 'data', 'vis', str)

    calfiles, caldir = bookkeeping.bookkeeping(visname)
    fields = bookkeeping.get_field_ids(taskvals['fields'])

    minbaselines = va(taskvals, 'crosscal', 'minbaselines', int, default=4)
    standard = va(taskvals, 'crosscal', 'standard', str, default='Stevens-Reynolds 2016')
    refant = va(taskvals, 'crosscal', 'refant', str, default='m059')
    calib_uvrange = va(taskvals, 'crosscal', 'calib_uvrange', str, default='')
    bpsmooth_kernel = va(taskvals, 'crosscal', 'bpsmooth_kernel', int, default=0)

    do_parallel_cal(visname, fields, calfiles, refant, caldir, minbaselines, standard,
        calib_uvrange=calib_uvrange, bpsmooth_kernel=bpsmooth_kernel)

if __name__ == '__main__':

    bookkeeping.run_script(main,logfile)
