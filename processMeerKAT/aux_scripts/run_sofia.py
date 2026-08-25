#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

import os, sys, time, re
import config_parser
import bookkeeping
import sofia_engine
from shutil import copyfile

THIS_PROG = __file__
DEF_DIR = os.path.abspath(os.path.join(os.path.dirname(THIS_PROG), '..'))
SOFCONFIG ='default_cont_sofmask.txt'

import logging
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s", level=logging.INFO)

def get_imagename(visname, loop):
    """Derive the '<basename>_im_<loop>' image prefix from the configured vis name.

    Mirrors the SPW-stripping regex bookkeeping.get_selfcal_args() uses (strips e.g.
    '.1410~1420.0MHz.' out of the basename) rather than a fixed-position str.split('.'),
    which breaks whenever the frequency range itself contains a '.' (e.g. '...1420.0MHz...'
    shifts every subsequent split index by one) -- confirmed this was silently producing a
    nonexistent '<vis>.0MHz_im_<N>.fits' filename for a vis like
    '1738276790.1410~1420.0MHz.NGC4064.mms', instead of the real
    '1738276790.NGC4064_im_<N>.fits'. Doesn't use msmetadata (unlike
    bookkeeping.get_selfcal_args()'s equivalent) since this script runs inside the SoFiA
    container, which has no CASA -- fine here because by the time run_sofia.py runs, [data]
    vis is always split.py's target-specific output (already has the field name embedded,
    e.g. '...NGC4064.mms'), never the pre-split crosscal MMS/MS that would need an
    msmetadata-derived field name substituted in."""

    visbase = os.path.split(visname.rstrip('/ '))[1]
    visbase = re.sub(r'\.\d+\.*\d*\~\d+\.*\d*[a-z,A-Z]?[Hz,hz,hZ,HZ]*\.', '.', visbase)
    basename = visbase.replace('.mms', '').replace('.ms', '')
    return '{0}_im_{1}'.format(basename, loop)

def main(args,taskvals):

    visname = config_parser.validate_args(taskvals, "data", "vis", str)
    loop = config_parser.validate_args(taskvals, "selfcal", "loop", int, default=2)
    imagename = get_imagename(visname, loop-1)

    #Copy default config to current location
    paramfile=os.path.join(os.path.dirname(visname),'cont_sofmask.txt')
    if not os.path.exists(paramfile):
        copyfile('{0}/{1}'.format(DEF_DIR,SOFCONFIG),paramfile)
    
    updates={'input.data':'{0}.fits'.format(imagename)}
             #,'output.directory': f'{os.path.dirname(visname)}/'}
    sofia_engine.update_sofia_config(paramfile,updates)

    sofia_engine.run_sofia(paramfile)
    
if __name__ == '__main__':
    
    args = config_parser.parse_args()
    taskvals, config = config_parser.parse_config(args['config'])
    bookkeeping.run_script(main)
    
    visname = config_parser.validate_args(taskvals, "data", "vis", str)
    loop = config_parser.validate_args(taskvals, "selfcal", "loop", int)
    imagename = get_imagename(visname, loop-1)

    config_parser.overwrite_config(args['config'], conf_dict={'usermask' : "'{0}_mask.fits'".format(imagename)}, conf_sec='selfcal')    
