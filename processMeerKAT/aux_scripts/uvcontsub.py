#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import config_parser
from config_parser import validate_args as va
import bookkeeping

import os,sys
import casampi
from casatasks import uvcontsub_old,casalog
logfile=casalog.logfile()
casalog.setlogfile('logs/{SLURM_JOB_NAME}-{SLURM_JOB_ID}.casa'.format(**os.environ))

def do_uvcontsub(vis,fitspw,fitorder):

    outputvis = vis + '.contsub'
    #uvcontsub(vis=vis,outputvis=outputvis,datacolumn='corrected',fitspec=fitspw,combine='',solint='int',fitorder=fitorder)
    excludechans = True
    if fitspw == '':
        excludechans = False
    uvcontsub_old(vis=vis,fitspw=fitspw,excludechans=excludechans,combine='',solint='int',fitorder=fitorder,want_cont=True)
    return outputvis

def main(args,taskvals):

    visname = va(taskvals, "data", "vis", str)
    fitspw = va(taskvals, "image", "fitspw", str)
    fitorder = va(taskvals, "image", "fitorder", int)
    
    newvis = do_uvcontsub(visname,fitspw,fitorder)

    config_parser.overwrite_config(args['config'], conf_dict={'vis' : "'{0}'".format(newvis)}, conf_sec='data')
    config_parser.overwrite_config(args['config'], conf_dict={'continuum_vis': "'{0}'".format(visname)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

if __name__ == '__main__':

    bookkeeping.run_script(main)