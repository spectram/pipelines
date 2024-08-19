#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import os, sys, time
import config_parser
import bookkeeping
from shutil import copyfile
import subprocess

THIS_PROG = __file__
DEF_DIR = os.path.abspath(os.path.join(os.path.dirname(THIS_PROG), '..'))
SOFCONFIG ='default_cont_sofmask.txt'

import logging
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def parse_sofia_config(file_path):
    config = {}
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip() # Removes ehitespace
            if not line or line.startswith('#'): # Deletes comments and empty lines
                continue
            if '=' in line:
                key, value = map(str.strip, line.split('=', 1))
                if value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                elif value.isdigit():
                    value = int(value)
                config[key] = value
    return config

def update_sofia_config(file_path, updates):
    """
    Update specific keys in a configuration file with new values.

    Parameters:
    - file_path: The path to the configuration file.
    - updates: A dictionary where keys are the configuration keys to update,
                and values are the new values to set.
    """
    # Read the original file content
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Open the file in write mode to update it
    with open(file_path, 'w') as file:
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _ = map(str.strip, line.split('=', 1))
                if key in updates:
                    file.write(f"{key} = {updates[key]}\n")
                else:
                    file.write(f"{line}\n")
            else:
                file.write(f"{line}\n")

def run_sofia(paramfile):
    
    command = "sofia "+ paramfile
    result = subprocess.run(command, shell=True, text=True, capture_output=False)

    # print("STDOUT:", result.stdout)
    # print("STDERR:", result.stderr)
    # print("Return Code:", result.returncode)
    
def main(args,taskvals):

    visname = config_parser.validate_args(taskvals, "data", "vis", str)
    loop = config_parser.validate_args(taskvals, "selfcal", "loop", int, default=2)
    imagename = f"{visname.split('.')[0]}.{visname.split('.')[2]}_im_{loop-1}"
        
    #Copy default config to current location    
    paramfile=os.path.join(os.path.dirname(visname),'cont_sofmask.txt')
    if not os.path.exists(paramfile):
        copyfile('{0}/{1}'.format(DEF_DIR,SOFCONFIG),paramfile)
    
    updates={'input.data':'{0}.fits'.format(imagename)}
             #,'output.directory': f'{os.path.dirname(visname)}/'}
    update_sofia_config(paramfile,updates)
    
    run_sofia(paramfile)
    
if __name__ == '__main__':
        
    bookkeeping.run_script(main)
    
    args = config_parser.parse_args()
    taskvals, config = config_parser.parse_config(args['config'])
    visname = config_parser.validate_args(taskvals, "data", "vis", str)
    loop = config_parser.validate_args(taskvals, "selfcal", "loop", int, default=2)
    imagename = f"{visname.split('.')[0]}.{visname.split('.')[2]}_im_{loop-1}"
      
    config_parser.overwrite_config(args['config'], conf_dict={'usermask' : "'{0}_mask.fits'".format(imagename)}, conf_sec='selfcal')    
