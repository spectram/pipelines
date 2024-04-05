#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import os
from config_parser import validate_args as va
import bookkeeping
from casatasks import casalog,uvsub
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

def do_uvsub(vis):

    uvsub(vis=vis)

def main(args,taskvals):

    visname = va(taskvals, "data", "vis", str)
    do_uvsub(visname)

if __name__ == '__main__':

    bookkeeping.run_script(main,logfile)
