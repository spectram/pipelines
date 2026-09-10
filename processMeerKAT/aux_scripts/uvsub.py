#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import os
import config_parser
from config_parser import validate_args as va
import bookkeeping
import shutil
from casatasks import casalog,uvsub
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

def do_uvsub(vis):

    uvsub(vis=vis)

def main(args,taskvals):

    visname = va(taskvals, "data", "vis", str)
    post_selfcal_vis = visname+'.post_selfcal'
    try:
        shutil.copytree(visname, post_selfcal_vis)
        print(f"Backup created successfully from {visname} to {post_selfcal_vis}.")
    except FileExistsError:
        print(f"Backup directory {post_selfcal_vis} already exists.")

    #Record the backup's path so science_image.py (via bookkeeping.get_post_selfcal_vis()) can find
    #the pre-uvsub, fully self-calibrated visibilities for continuum imaging -- uvsub() below modifies
    #'[data] vis' 's CORRECTED_DATA in place, so nothing downstream should keep reading '[data] vis'
    #directly for continuum imaging once this has run. See REFACTOR_PLAN.md's Phase 10b write-up.
    config_parser.overwrite_config(args['config'], conf_dict={'post_selfcal_vis' : "'{0}'".format(post_selfcal_vis)},
        conf_sec='run', sec_comment='# Internal variables for pipeline execution')

    do_uvsub(visname)

if __name__ == '__main__':

    bookkeeping.run_script(main,logfile)
