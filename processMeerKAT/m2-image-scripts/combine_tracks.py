import glob
from casatasks import virtualconcat
from shutil import copytree
import os

target = os.path.basename(os.path.dirname(os.getcwd()))
print(target)

targetdir = '/scratch3/projects/meerrings/' + target
p1ms = glob.glob(targetdir + "/p1/*." + target + ".mms.contsub")[0]
p2ms = glob.glob(targetdir + "/p2/*." + target + ".mms.contsub")[0]
print(p1ms)
print(p2ms)
m2dir=targetdir+'/m2/'
m2ms = m2dir+target+"_M2.mms"

print('copying p2')
copytree(p2ms, m2dir+target+'_p2_contsub.mms', dirs_exist_ok=True)
print('Copying p1')
copytree(p1ms, m2dir+target+'_p1_contsub.mms', dirs_exist_ok=True)
print('virtualconcat for m2')
virtualconcat(vis=[m2dir+target+'_p1_contsub.mms', m2dir+target+'_p2_contsub.mms'], concatvis=m2ms, keepcopy=False)
