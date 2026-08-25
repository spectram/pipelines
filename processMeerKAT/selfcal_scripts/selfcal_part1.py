#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import sys
import os
import glob
import shutil

import config_parser
from config_parser import validate_args as va
import bookkeeping
import selfcal_stages

from casatasks import *
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))
import casampi

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def selfcal_part1(vis, refant, dopol, stages, loop, cell, robust, imsize, wprojplanes, uvrange, nterms,
                  gridder, deconvolver, discard_nloops, gaintype, outlier_threshold, outlier_radius, flag, \
                      atrous_do,flag_maxsize_bm, scales, usermask, pb_correct=False, pbthreshold=0.1, pbband='LBand'):

    imbase,imagename,outimage,pixmask,rmsfile,caltable,prev_caltables,threshold,outlierfile,cfcache,_,_,_,_ = bookkeeping.get_selfcal_args(vis,loop,stages,nterms,\
        deconvolver,discard_nloops,outlier_threshold,outlier_radius,usermask=usermask,step='tclean')

    if os.path.exists(outlierfile) and open(outlierfile).read() == '':
        outlierfile = ''

    #Add model column with MPI rather than in selfcal_part2 without MPI.
    #Assumes you've split out your corrected data from crosscal
    if loop == 0:
        clearcal(vis=vis, addmodel=True)

    if selfcal_stages.should_apply_prev_cal(stages, loop) and len(prev_caltables) > 0:
        applycal(vis=vis, selectdata=False, gaintable=prev_caltables, parang=False, interp='linear,linearflag')

        if flag:
            flagdata(vis=vis, mode='rflag', datacolumn='RESIDUAL', field='', timecutoff=5.0,
                    freqcutoff=5.0, timefit='line', freqfit='line', flagdimension='freqtime',
                    extendflags=False, timedevscale=3.0, freqdevscale=3.0, spectralmax=500,
                    extendpols=False, growaround=False, flagneartime=False, flagnearfreq=False,
                    action='apply', flagbackup=True, overwrite=True, writeflags=True)

    if os.path.exists(outimage):
        logger.info('Image "{0}" exists. Not overwriting, continuing to next loop.'.format(outimage))
        exit(0)
    else:
        # calcpsf is always True: reusing a symlinked PSF/weight density from a previous loop
        # (calcpsf=False) breaks tclean's parallel (MPI) major cycle -- the per-engine data
        # selection needed to apply the weights during the major cycle is only registered when
        # the PSF is actually (re)computed, so skipping it raises "Imaging weight calculation is
        # requested for a data that was not selected" partway through the major cycle. The PSF
        # recompute this forces costs ~30s (see tclean's setup/weight-density steps), negligible
        # next to the major cycle itself.
        #
        # Since calcpsf is always True now, there's no scenario where reusing a *previous attempt's*
        # leftover products for this same loop is intentional -- so remove them before running.
        # Confirmed by a real crash+retry: a stale imagename.psf/.sumwt symlink left over from a
        # crashed run of the old (now-removed) PSF-reuse code was still present -- unrelated to and
        # untouched by calcpsf -- on the next attempt. tclean's restart=True path found that
        # pre-existing .psf/.sumwt (pointing at a *different* image's finalized weights) and, despite
        # calcpsf=True, ended up with inconsistent per-engine PSF/weight registration, reproducing
        # the identical "Imaging weight calculation is requested for a data that was not selected"
        # error -- this time raised from makepsf() itself. Any partial products from an earlier
        # crashed attempt at this exact loop (psf/sumwt/gridwt_temp/workdirectory/etc, all sharing
        # the imagename prefix) are removed so every attempt starts genuinely clean.
        for product in glob.glob(imagename + '.*'):
            if os.path.islink(product) or os.path.isfile(product):
                os.remove(product)
            else:
                shutil.rmtree(product)
        tclean(vis=vis, selectdata=False, datacolumn='corrected', imagename=imagename,
            imsize=imsize, cell=cell, stokes='I', gridder=gridder,
            wprojplanes = wprojplanes, deconvolver = deconvolver, restoration=True,
            weighting='briggs', robust = robust, niter=stages[loop].niter, outlierfile=outlierfile,
            threshold=threshold, nterms=nterms, calcpsf=True, # cfcache = cfcache,
            pblimit=-1, mask=pixmask, parallel = True, scales=scales)

if __name__ == '__main__':

    args,params = bookkeeping.get_selfcal_params()
    selfcal_part1(**params)
    bookkeeping.rename_logs(logfile)
