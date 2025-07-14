# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
# https://skaafrica.atlassian.net/wiki/spaces/ESDKB/pages/277315585/MeerKAT+specifications#Defining-the-simplified-primary-beam-model-in-CASA

def beam (offsetx, offsety, freq):
    # freq in MHz offsetx, offsety in degrees
    FWHM = np.sqrt(89.5*86.2)/60.0*(1e3/freq)
    theta = np.sqrt(offsetx**2 + offsety**2)
    a = (np.cos(1.189*np.pi*(theta/FWHM))/(1 - 4*(1.189*(theta/FWHM))**2))**2
    return a

MEERKAT_PB = vp.setpbnumeric(
  telescope = 'OTHER',
  othertelescope = 'MeerKAT',
  dopb = True,
  vect = beam(np.linspace(0,3.0, 1000), 0, 1400.0),
  maxrad = '3.0 deg',
  reffreq = '1.4 GHz',
  isthispb = 'PB',
  dosquint = False)
vp.saveastable('MEERKAT_PB.tab')

target='AM1724-622' ### Update target
imspw='*:1393~1403MHz' ### UPDATE SPW
mms="/scratch3/projects/meerrings/"+target+"/m2/"+target+"_M2.mms" ### UPDATE MMS
trial="m2h1"
nit=1500000 
thresh='0.24mJy'
imname=target+'_'+trial ### UPDATE IMNAME

cleanmask=imname[:-1]+'0-sofmask.im'
default(importfits)
importfits(fitsimage='../m2h0/'+imname[:-1]+'0.image_mask.fits', imagename=cleanmask, defaultaxes=True, defaultaxesvalues=['','','','I'])

default(tclean)
summary = tclean(vis=mms, imagename=imname, field='0', specmode='cube', restfreq='1420.4057517667MHz', veltype='optical', outframe='bary', deconvolver='multiscale', scales=[0,3,5,10], gridder='wproject', wprojplanes=256, restoringbeam='', pblimit=-1e-16, niter=nit, gain=0.1, threshold=thresh, imsize=[2048,2048], cell='2.0arcsec', weighting='briggs', robust=1.0, savemodel='none', interactive=0, parallel=True, pbcor=True, usemask='user',spw=imspw, mask=cleanmask, vptable='MEERKAT_PB.tab') 

f = open(imname+".txt","w")
f.write(str(summary))
f.close()

cube=imname+'.image'

default(exportfits)
# If running m2h2 then you have to preserve the spectral axis when you exportfts
exportfits(imagename=cube, fitsimage=imname+'.image.fits', overwrite=True, dropdeg=True, dropstokes=True)

default(immoments)
immoments(cube, axis='spec', moments=[0], includepix=[0.0006,100.0], outfile=imname+'.6mJy.mom0', chans='3~380')

## Fincubes
fincubedir='../fincubes/'
exportfits(imagename=cube, fitsimage=fincubedir+imname+'_im.fits', overwrite=True, dropdeg=True, dropstokes=True, velocity=True, optical=True)
exportfits(imagename=imname+'.pb', fitsimage=fincubedir+imname+'_pb.fits', overwrite=True, dropdeg=True, dropstokes=True, velocity=True, optical=True)

## Rebin
default(imrebin)
imrebin(imagename=cube, outfile=imname+'_rebin.im',factor=[2,2,1],dropdeg=True,overwrite=True,crop=True, chans='3~380')
exportfits(imagename=imname+'_rebin.im', fitsimage=fincubedir+imname+'_rebin_im.fits', overwrite=True, dropdeg=True, dropstokes=True, velocity=True, optical=True)

imrebin(imagename=imname+'.pb', outfile=imname+'_rebin.pb',factor=[2,2,1],dropdeg=True,overwrite=True,crop=True, chans='3~380')
exportfits(imagename=imname+'_rebin.pb', fitsimage=fincubedir+imname+'_rebin_pb.fits', overwrite=True, dropdeg=True, dropstokes=True, velocity=True, optical=True)

