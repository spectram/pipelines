#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""
Runs partition on the input MS
"""
import sys
import os
import glob

import config_parser
from config_parser import validate_args as va
import read_ms
import processMeerKAT
import bookkeeping

from casatasks import *
logfile=casalog.logfile()
from casatools import msmetadata
import casampi
msmd = msmetadata()

def do_partition(visname, spw, preavg, CPUs, include_crosshand, createmms, spwname):
    # Get the .ms bit of the filename, case independent
    basename, ext = os.path.splitext(visname)
    filebase = os.path.split(basename)[1]
    extn = 'mms' if createmms else 'ms'

    mvis = '{0}.{1}.{2}'.format(filebase,spwname,extn)
    nscan = 1 if not createmms else msmd.nscans()
    chanaverage = True if preavg > 1 else False
    correlation = '' if include_crosshand else 'XX,YY'

    mstransform(vis=visname, outputvis=mvis, spw=spw, createmms=createmms, datacolumn='DATA', chanaverage=chanaverage, chanbin=preavg,
                numsubms=nscan, separationaxis='scan', keepflags=True, usewtspectrum=True, nthreads=CPUs, antenna='*&', correlation=correlation)

    return mvis

def main(args,taskvals):

    visname = va(taskvals, 'data', 'vis', str)
    calcrefant = va(taskvals, 'crosscal', 'calcrefant', bool, default=False)
    refant = va(taskvals, 'crosscal', 'refant', str, default='m005')
    spw = va(taskvals, 'crosscal', 'spw', str, default='')
    nspw = va(taskvals, 'crosscal', 'nspw', int, default='')
    tasks = va(taskvals, 'slurm', 'ntasks_per_node', int)
    preavg = va(taskvals, 'crosscal', 'chanbin', int, default=1)
    include_crosshand = va(taskvals, 'run', 'dopol', bool, default=False)
    createmms = va(taskvals, 'crosscal', 'createmms', bool, default=True)

    if nspw > 1:
        casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_ARRAY_JOB_ID}_{SLURM_ARRAY_TASK_ID}.casa'.format(**os.environ))
    else:
        logfile=casalog.logfile()
        casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

    if ',' in spw:
        low,high,unit,dirs = config_parser.parse_spw(args['config'])
        spwname = '{0:.0f}~{1:.0f}MHz'.format(min(low),max(high))
    else:
        spwname = spw.replace('*:','')

    msmd.open(visname)
    #msmd.ncorrforpol() returns a numpy scalar (numpy int32/int64), not a plain Python int.
    #Left uncast, CPUs below inherits that numpy dtype, and passing it to mstransform's
    #nthreads= means CASA's MPI layer serializes the call (to ship to remote worker ranks) with
    #NumPy 2.x's scalar repr, e.g. 'nthreads=np.int64(2)' -- which then fails with
    #NameError: name 'np' is not defined on every rank when eval'd remotely (no numpy import in
    #that namespace). mstransform() itself doesn't raise on this -- confirmed live: every rank's
    #sub-MS creation failed, but the job still exited 0/COMPLETED having produced an empty MMS,
    #only surfacing as a confusing failure in the next script (validate_input.py, unable to open
    #the nonexistent table). int() here (and again on npol itself, since it feeds the same
    #comparison/assignment) prevents this at the source.
    npol = int(msmd.ncorrforpol()[0])

    if not include_crosshand and npol == 4:
        npol = 2
    CPUs = int(npol if tasks*npol <= processMeerKAT.CPUS_PER_NODE_LIMIT else 1) #hard-code for number of polarisations

    mvis = do_partition(visname, spw, preavg, CPUs, include_crosshand, createmms, spwname)

    #mstransform() can silently fail per-rank (as above) without raising here -- confirmed live.
    #Check real output exists before declaring success, rather than letting an empty MMS pass
    #through to every downstream script as if partitioning had actually worked.
    if not os.path.exists(mvis):
        raise RuntimeError("mstransform() did not produce output '{0}'.".format(mvis))
    if createmms and len(glob.glob('{0}/SUBMSS/*'.format(mvis))) == 0:
        raise RuntimeError("mstransform() produced '{0}' but it has no SUBMSS -- partitioning "
            "silently failed on every MPI rank (check logs/*.mpi and logs/*.err for the real "
            "per-rank error).".format(mvis))

    mvis = "'{0}'".format(mvis)
    vis = "'{0}'".format(visname)

    config_parser.overwrite_config(args['config'], conf_sec='data', conf_dict={'vis':mvis})
    config_parser.overwrite_config(args['config'], conf_sec='run', sec_comment='# Internal variables for pipeline execution', conf_dict={'orig_vis':vis})
    msmd.done()

if __name__ == '__main__':

    bookkeeping.run_script(main,logfile)
