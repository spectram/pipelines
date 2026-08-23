#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

#!/usr/bin/env python3

import sys
import traceback

import config_parser
import selfcal_stages
from collections import namedtuple
import os
import glob
import re

import logging
from time import gmtime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def get_calfiles(visname, caldir):
        base = os.path.splitext(visname)[0]
        kcorrfile = os.path.join(caldir,base + '.kcal')
        bpassfile = os.path.join(caldir,base + '.bcal')
        gainfile =  os.path.join(caldir,base + '.gcal')
        dpolfile =  os.path.join(caldir,base + '.pcal')
        xpolfile =  os.path.join(caldir,base + '.xcal')
        xdelfile =  os.path.join(caldir,base + '.xdel')
        fluxfile =  os.path.join(caldir,base + '.fluxscale')

        calfiles = namedtuple('calfiles',
                ['kcorrfile', 'bpassfile', 'gainfile', 'dpolfile', 'xpolfile',
                    'xdelfile', 'fluxfile'])
        return calfiles(kcorrfile, bpassfile, gainfile, dpolfile, xpolfile,
                xdelfile, fluxfile)


def bookkeeping(visname):
    # Book keeping
    caldir = os.path.join(os.getcwd(), 'caltables')
    calfiles = get_calfiles(visname, caldir)

    return calfiles, caldir

def get_field_ids(fields):
    """
    Given an input list of source names, finds the associated field
    IDS from the MS and returns them as a list.
    """

    targetfield    = fields['targetfields']
    extrafields    = fields['extrafields']
    fluxfield      = fields['fluxfield']
    bpassfield     = fields['bpassfield']
    secondaryfield = fields['phasecalfield']
    kcorrfield     = fields['phasecalfield']
    xdelfield      = fields['phasecalfield']
    dpolfield      = fields['phasecalfield']
    xpolfield      = fields['phasecalfield']

    if fluxfield != secondaryfield:
        gainfields = \
                str(fluxfield) + ',' + str(secondaryfield)
    else:
        gainfields = str(fluxfield)

    FieldIDs = namedtuple('FieldIDs', ['targetfield', 'fluxfield',
                    'bpassfield', 'secondaryfield', 'kcorrfield', 'xdelfield',
                    'dpolfield', 'xpolfield', 'gainfields', 'extrafields'])

    return FieldIDs(targetfield, fluxfield, bpassfield, secondaryfield,
            kcorrfield, xdelfield, dpolfield, xpolfield, gainfields, extrafields)

def polfield_name(visname):

    from casatools import msmetadata
    msmd = msmetadata()
    msmd.open(visname)
    fieldnames = msmd.fieldnames()
    msmd.done()

    polfield = ''
    if any([ff in ["3C286", "1328+307", "1331+305", "J1331+3030"] for ff in fieldnames]):
        polfield= list(set(["3C286", "1328+307", "1331+305", "J1331+3030"]).intersection(set(fieldnames)))[0]
    elif any([ff in ["3C138", "0518+165", "0521+166", "J0521+1638"] for ff in fieldnames]):
        polfield = list(set(["3C138", "0518+165", "0521+166", "J0521+1638"]).intersection(set(fieldnames)))[0]
    elif any([ff in ["3C48", "0134+329", "0137+331", "J0137+3309"] for ff in fieldnames]):
        polfield = list(set(["3C48", "0134+329", "0137+331", "J0137+3309"]).intersection(set(fieldnames)))[0]
    elif "J1130-1449" in fieldnames:
        polfield = "J1130-1449"
    else:
        logger.warning("No valid polarization field found. Defaulting to use the phase calibrator to solve for XY phase.")
        logger.warning("The polarization solutions found will likely be wrong. Please check the results carefully.")

    return polfield

def check_file(filepath):

    # Python2 only has IOError, so define FileNotFound
    try:
        FileNotFoundError
    except NameError:
        FileNotFoundError = IOError

    if not os.path.exists(filepath):
        logger.error('Calibration table "{0}" was not written. Please check the CASA output and whether a solution was found.'.format(filepath))
        raise FileNotFoundError
    else:
        logger.info('Calibration table "{0}" successfully written.'.format(filepath))

def get_selfcal_params():

    """Parse the '[selfcal]' config section into kwargs for selfcal_part1()/
    selfcal_part2()/find_outliers()/mask_image(), resolving 'stages' (a list of dicts, one
    per self-cal loop -- see selfcal_stages.py) into a list of selfcal_stages.Stage.

    Previously this also broadcast every scalar '[selfcal]' value into an ('nloops'+1)-long
    list (the 'single_args'/'gaincal_args'/'list_args' machinery, deleted here -- see
    REFACTOR_PLAN.md's Phase 2 write-up), so that per-loop-varying and never-varying
    parameters alike were indexed the same way ('param[loop]') downstream. Now only the
    keys that genuinely vary per loop (mask/apply_cal/derive_cal/niter/threshold/solint)
    live in 'stages'; every other '[selfcal]' key is used directly as the plain scalar (or
    list, e.g. imsize=[6144,6144]) the user configured, with no implicit replication and no
    'nloops'-length validation to get wrong."""

    # Get the name of the config file
    args = config_parser.parse_args()

    # Parse config file
    taskvals, config = config_parser.parse_config(args['config'])
    params = taskvals['selfcal']

    params['vis'] = taskvals['data']['vis']
    params['refant'] = taskvals['crosscal']['refant']
    params['dopol'] = taskvals['run']['dopol']

    if params['dopol'] and 'G' in params['gaintype']:
        logger.warning("dopol is True, but gaintype includes 'G'. Use gaintype='T' for polarisation on linear feeds (e.g. MeerKAT).")

    try:
        params['stages'] = selfcal_stages.parse_stages(params['stages'])
    except ValueError as err:
        logger.error("Invalid 'stages' in '{0}': {1}".format(args['config'], err))
        sys.exit(1)

    return args,params

def get_selfcal_args(vis,loop,stages,nterms,deconvolver,discard_nloops,\
    outlier_threshold,outlier_radius,step,usermask):

    """Resolve this loop's file/parameter bookkeeping from the stage list ('stages', a list
    of selfcal_stages.Stage as returned by get_selfcal_params()) instead of indexing into
    parallel arrays. 'nloops' is derived from len(stages), not passed separately; 'calmode'
    and 'threshold' come from stages[loop] rather than being separate loop-indexed
    arguments -- see selfcal_stages.py and REFACTOR_PLAN.md's Phase 2 write-up."""

    from casatools import msmetadata,quanta
    from read_ms import check_spw
    msmd = msmetadata()
    qa = quanta()

    nloops = selfcal_stages.nloops(stages)
    stage = stages[loop]

    if os.path.exists('{0}/SUBMSS'.format(vis)):
        tmpvis = glob.glob('{0}/SUBMSS/*'.format(vis))[0]
    else:
        tmpvis = vis

    msmd.open(tmpvis)

    visbase = os.path.split(vis.rstrip('/ '))[1] # Get only vis name, not entire path
    visbase = re.sub('\.\d+\.*\d*\~\d+\.*\d*[a-z,A-Z]?[Hz,hz,hZ,HZ]*\.','.',visbase) # Strip any SPWs from basename (when running outlier imaging separately per SPW)
    targetfields = config_parser.get_key(config_parser.parse_args()['config'], 'fields', 'targetfields')

    #Force taking first target field (relevant for writing outliers.txt at beginning of pipeline)
    if type(targetfields) is str and ',' in targetfields:
        targetfield = targetfields.split(',')[0]
        msg = 'Multiple target fields input ("{0}"), but only one position can be used to identify outliers (for outlier imaging). Using "{1}".'
        logger.warning(msg.format(targetfields,targetfield))
    else:
        targetfield = targetfields
    #Make sure it's an integer
    try:
        targetfield = int(targetfield)
    except ValueError: # It's not an int, but a str
        targetfield = msmd.fieldsforname(targetfield)[0]

    target_str = msmd.namesforfields(targetfield)[0]

    if '.ms' in visbase and target_str not in visbase:
        basename = visbase.replace('.ms','.{0}'.format(target_str))
    else:
        basename = visbase.replace('.mms', '')

    imbase = basename + '_im_%d' # Images will be produced in $CWD
    imagename = imbase % loop
    outimage = imagename + '.image'
    pixmask = imagename + ".pixmask"
    maskfile = imagename + ".islmask"
    rmsfile = imagename + ".rms"
    caltable = basename + '.gcal%d' % loop
    prev_caltables = sorted(glob.glob('*.gcal?'))
    cfcache = basename + '.cf'
    thresh = 10

    if deconvolver == 'mtmfs':
        outimage += '.tt0'

    if step not in ['tclean','sky'] and not os.path.exists(outimage):
        logger.error("Image '{0}' doesn't exist, so self-calibration loop {1} failed. Will terminate selfcal process.".format(outimage,loop))
        sys.exit(1)

    if step in ['tclean','predict']:
        pixmask = selfcal_stages.resolve_mask(stages, loop, imbase)
        rmsfile = imbase % (loop-1) + '.rms'
    #Loop 0 (the dirty image) never has a previous-loop mask to fall back on; stage 0 is
    #validated (selfcal_stages.parse_stages()) to never reference 'prev', so pixmask is
    #already '' here for loop 0 via resolve_mask() above -- this check is a no-op for
    #'tclean'/'predict' and only actually matters for 'sky' (see set_sky_model.py), where
    #pixmask is still this loop's own not-yet-built mask file rather than a resolved 'prev'
    #reference. Previously also blanked pixmask for an intermediate loop with an empty
    #calmode ('0 < loop < nloops and calmode[loop] == \'\''); that's now the config
    #author's explicit choice (mask=None on that stage) rather than something inferred here.
    if step in ['tclean','predict','sky'] and (loop == 0 and not os.path.exists(pixmask)):
        pixmask = ''
    if (loop >= nloops) and (usermask!=''):
        if '.fits' in usermask:
            from casatasks import importfits, imhead
            cenfreq = imhead(imagename=imbase%(loop-1)+'.image', mode="get", hdkey="crval4")
            if cenfreq['unit'] == 'Hz':
                cenfreq_str= str(round(cenfreq['value']/1e9,4))+ 'GHz'  #convert Hz to GHz
            else:
                print("CUNIT4 is not Hz?",cenfreq)
            immask=usermask.replace('.fits','.im')
            importfits(fitsimage=usermask, imagename=immask, defaultaxes=True, \
                defaultaxesvalues=['','',cenfreq_str,'I'], overwrite=True) 
            usermask=immask
            args = config_parser.parse_args()
            config_parser.overwrite_config(args['config'], conf_dict={'usermask' : '"{0}"'.format(usermask)}, conf_sec='selfcal')

        pixmask=usermask

    #Check no missing caltables
    for i in range(0,loop):
        if stages[i].derive_cal != '' and not os.path.exists(basename + '.gcal%d' % i):
            logger.error("Calibration table '{0}' doesn't exist, so self-calibration loop {1} failed. Will terminate selfcal process.".format(basename + '.gcal%d' % i,i))
            sys.exit(1)
    for i in range(discard_nloops):
        prev_caltables.pop(0)

    if outlier_threshold != '' and outlier_threshold != 0: # and (loop > 0 or step in ['sky','bdsf'] and loop == 0):
        if step in ['tclean','predict','sky']:
            outlierfile = 'outliers_loop{0}.txt'.format(loop)
        else:
            outlierfile = 'outliers_loop{0}.txt'.format(loop+1)

        #Derive sky model radius for outliers, assuming channel 0 (of SPW 0) is lowest frequency and therefore largest FWHM
        if outlier_radius == 0.0 or outlier_radius == '' and step == 'sky':
            SPW = check_spw(config_parser.parse_args()['config'],msmd)
            low_freq = float(SPW.replace('*:','').split('~')[0]) * 1e6 #MHz to Hz
            rads=1.025*qa.constants(v='c')['value']/low_freq/ msmd.antennadiameter()['0']['value']
            FWHM=qa.convert(qa.quantity(rads,'rad'),'deg')['value']
            sky_model_radius = 1.5*FWHM #degrees
            logger.warning('Using calculated search radius of {0:.1f} degrees.'.format(sky_model_radius))
        else:
            if step == 'sky':
                logger.info('Using preset search radius of {0} degrees'.format(outlier_radius))
            sky_model_radius = outlier_radius
    else:
        outlierfile = ''
        sky_model_radius = 0.0

    msmd.done()

    threshold = stage.threshold
    if not (type(threshold) is str and 'Jy' in threshold) and threshold > 1:
        if step in ['tclean','predict']:
            if os.path.exists(rmsfile):
                from casatasks import imstat
                stats = imstat(imagename=rmsfile)
                threshold = threshold * stats['min'][0]
            else:
                logger.error("'{0}' doesn't exist. Can't do thresholding at S/N > {1}. Loop 0 must use an absolute threshold value. Check the logs to see why RMS map not created.".format(rmsfile,threshold))
                sys.exit(1)
        elif step == 'bdsf':
            thresh = threshold

    return imbase,imagename,outimage,pixmask,rmsfile,caltable,prev_caltables,threshold,outlierfile,\
        cfcache,thresh,maskfile,targetfield,sky_model_radius

def rename_logs(logfile=''):

    if logfile != '' and os.path.exists(logfile):
        if 'SLURM_ARRAY_JOB_ID' in os.environ:
            IDs = '{SLURM_JOB_NAME}-{SLURM_ARRAY_JOB_ID}_{SLURM_ARRAY_TASK_ID}'.format(**os.environ)
        else:
            IDs = '{SLURM_JOB_NAME}-{SLURM_JOB_ID}'.format(**os.environ)

        os.rename(logfile,'logs/{0}.mpi'.format(IDs))
        for log in glob.glob('*.last'):
            os.rename(log,'logs/{0}-{1}.last'.format(os.path.splitext(log)[0],IDs))

def get_imaging_params():

    # Get the name of the config file
    args = config_parser.parse_args()

    # Parse config file
    taskvals, config = config_parser.parse_config(args['config'])
    params = taskvals['image']
    params['vis'] = taskvals['data']['vis']
    params['keepmms'] = taskvals['crosscal']['keepmms']
    params['spw'] = taskvals['crosscal']['spw']
    params.pop('fitspw')
    params.pop('fitorder')
    params.pop('imspw')

    #Rename the masks that were already used
    if params['outlierfile'] != '' and os.path.exists(params['outlierfile']):
        outliers=open(params['outlierfile']).read()
        outlier_bases = re.findall(r'imagename=(.*)\n',outliers)
        for name in outlier_bases:
            mask = '{0}.mask'.format(name)
            if os.path.exists(mask):
                newname = '{0}.old'.format(mask)
                logger.info('Re-using old mask for "{0}". Renaming "{1}" to "{2}" to avoid mask conflict.'.format(name,mask,newname))
                os.rename(mask,newname)

    return args,params

def run_script(func,logfile=''):

    # Get the name of the config file
    args = config_parser.parse_args()

    # Parse config file
    taskvals, config = config_parser.parse_config(args['config'])

    continue_run = config_parser.validate_args(taskvals, 'run', 'continue', bool, default=True)
    spw = config_parser.validate_args(taskvals, 'crosscal', 'spw', str)
    nspw = config_parser.validate_args(taskvals, 'crosscal', 'nspw', int)

    if continue_run:
        try:
            func(args,taskvals)
            rename_logs(logfile)
        except Exception as err:
            logger.error('Exception found in the pipeline of type {0}: {1}'.format(type(err),err))
            logger.error(traceback.format_exc())
            config_parser.overwrite_config(args['config'], conf_dict={'continue' : False}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
            if nspw > 1:
                for SPW in spw.split(','):
                    spw_config = '{0}/{1}'.format(SPW.replace('*:',''),args['config'])
                    config_parser.overwrite_config(spw_config, conf_dict={'continue' : False}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
            rename_logs(logfile)
            sys.exit(1)
    else:
        logger.error('Exception found in previous pipeline job, which set "continue=False" in [run] section of "{0}". Skipping "{1}".'.format(args['config'],os.path.split(sys.argv[2])[1]))
        #os.system('./killJobs.sh') # and cancelling remaining jobs (scancel not found since /opt overwritten)
        rename_logs(logfile)
        sys.exit(1)
