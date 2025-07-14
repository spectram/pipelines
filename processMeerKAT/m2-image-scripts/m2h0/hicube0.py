# -*- coding: utf-8 -*-

target='AM1724-622' ### Update target
imspw='*:1393~1403MHz' ### UPDATE SPW
mms="/scratch3/projects/meerrings/"+target+"/m2/"+target+"_M2.mms" ### UPDATE MMS
trial="m2h0"
nit=50000
thresh='0.6mJy'
imname=target+'_'+trial ### UPDATE IMNAME

default(tclean)
summary = tclean(vis=mms, imagename=imname, field='0', specmode='cube', restfreq='1420.4057517667MHz', veltype='optical', outframe='bary', deconvolver='multiscale', scales=[0,3,5,10], gridder='wproject', wprojplanes=256, restoringbeam='', pblimit=-1e-16, niter=nit, gain=0.1, threshold=thresh, imsize=[2048,2048], cell='2.0arcsec', weighting='briggs', robust=1.0, savemodel='none', interactive=0, parallel=True, pbcor=False, usemask='user', spw=imspw)

f = open(imname+".txt","w")
f.write(str(summary))
f.close()

cube=imname+'.image'

default(exportfits)
exportfits(imagename=cube, fitsimage=imname+'.image.fits', overwrite=True, dropdeg=True, dropstokes=True)

default(immoments)
immoments(cube, axis='spec', moments=[0], includepix=[0.0006,100.0], outfile=imname+'.6mJy.mom0')
