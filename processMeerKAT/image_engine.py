#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Shared CASA imaging engine behind `hi_image.py` ([hi_image], HI cube imaging) and
`science_image.py` ([cont_image], continuum imaging) -- see "Phase 6" in
REFACTOR_PLAN.md. Two thin per-purpose entry scripts call into the functions here rather
than each re-implementing the same tclean/PB-correction/export logic, since the existing
`postcal_scripts` tuple format (script, threadsafe, container) has no room for
per-invocation extra args to let one script safely disambiguate two independently-gated,
independently-counted config sections at runtime.

Unlike `image_stages.py` (deliberately CASA-free, unit-testable with plain `python3`),
this module needs CASA -- mirrors `selfcal_part1.py`/`selfcal_part2.py`'s relationship to
`selfcal_stages.py`."""

import os
import shutil
import numpy as np

from casatasks import tclean, exportfits, imrebin, importfits, imhead
from casatools import image as _image_tool

import logging
logger = logging.getLogger(__name__)


def run_stage(vis, imagename, mask, niter, threshold, imsize, cell, robust, uvtaper, scales, gridder,
              wprojplanes, deconvolver, weighting, specmode, restfreq, spw, nterms, stokes, restoringbeam,
              outlierfile=''):

    """Run one imaging stage's `tclean` call. Idempotent (skips if the output image already
    exists), matching the pre-existing `science_image.py`/selfcal convention.

    Arguments:
    ----------
    vis : str
        Input MeasurementSet.
    imagename : str
        Base imagename for this stage (no extension) -- caller resolves per-stage/per-combo
        naming (e.g. `hi_image.py`'s `hi_combo{c}/stage{s}`).
    mask : str
        Path to a mask FITS file (from `image_stages.resolve_mask()`), or ''.
    niter, threshold : from this stage's ``image_stages.Stage``.
    imsize, cell, robust, uvtaper, scales, gridder, wprojplanes, deconvolver, weighting,
    specmode, restfreq, spw, nterms, stokes, restoringbeam :
        Passed straight through to `tclean()`.
    outlierfile : str, optional
        Passed straight through to `tclean()` (continuum-only -- see `[cont_image]
        outlierfile`; HI imaging doesn't use this).

    Returns:
    --------
    outimage : str
        Path to the produced CASA image (`.image`, or `.image.tt0` for `deconvolver='mtmfs'`)."""

    outimage = imagename + ('.image.tt0' if deconvolver == 'mtmfs' else '.image')

    if os.path.exists(outimage):
        logger.info('Image "{0}" already exists. Not overwriting, continuing to next step.'.format(outimage))
        return outimage

    #Matches both prior callers' behaviour: science_image.py never set usemask explicitly
    #(CASA's own tclean default is 'user'), and the prototype's dirty-stage call set
    #usemask='user' even with no mask -- i.e. an empty user mask, not e.g. 'pb'-mode masking.
    usemask = 'user'
    maskarg = ''
    if mask != '':
        #SoFiA writes a FITS island mask missing the frequency/Stokes axes tclean's
        #mask= needs -- import it to a CASA image with those axes restored, mirroring
        #bookkeeping.get_selfcal_args()'s identical usermask-import pattern. defaultaxes=True
        #requires defaultaxesvalues explicitly (confirmed via a real run: omitting it raises
        #TypeError, not a default) -- read the reference frequency from the previous stage's
        #own CASA image (image_stages.resolve_mask()'s naming: '<prev_imagename>_mask.fits'),
        #which has the full header the FITS mask lacks.
        immask = mask.replace('.fits', '.im')
        if not os.path.exists(immask):
            prev_image = mask[:-len('_mask.fits')] + '.image'
            cenfreq = imhead(imagename=prev_image, mode="get", hdkey="crval4")
            cenfreq_str = str(round(cenfreq['value']/1e9,4)) + 'GHz' if cenfreq['unit'] == 'Hz' else str(cenfreq['value']) + cenfreq['unit']
            importfits(fitsimage=mask, imagename=immask, defaultaxes=True,
                defaultaxesvalues=['','',cenfreq_str,'I'], overwrite=True)
        maskarg = immask
        usemask = 'user'

    kwargs = dict(vis=vis, selectdata=False, datacolumn='corrected', imagename=imagename,
        imsize=imsize, cell=cell, stokes=stokes, gridder=gridder, specmode=specmode,
        wprojplanes=wprojplanes, deconvolver=deconvolver, restoration=True,
        weighting=weighting, robust=robust, niter=niter, scales=scales,
        restfreq=restfreq, uvtaper=uvtaper, spw=spw, threshold=threshold, nterms=nterms,
        calcpsf=True, mask=maskarg, usemask=usemask, pbcor=False, pblimit=-1,
        restoringbeam=restoringbeam, gain=0.1, parallel=True, outlierfile=outlierfile)

    #Cube-specific tclean bug workaround, already established by science_image.py: disable
    #MPI parallelism for cube imaging.
    if specmode == 'cube':
        kwargs['parallel'] = False
        kwargs['veltype'] = 'optical'
        kwargs['outframe'] = 'bary'

    tclean(**kwargs)

    return outimage


def do_pb_corr(inpimage, pbthreshold=0, pbband='LBand'):

    """Given the input CASA image, outputs a katbeam-corrected image, optionally masked
    below a threshold. Moved here (from `science_image.py`) since selfcal's own final loop
    now also calls this -- see REFACTOR_PLAN.md's Phase 6 write-up.

    Arguments:
    ----------
    inpimage : str
        Input CASA image name.
    pbthreshold : float, optional
        Cutoff threshold to mask the PB.
    pbband : str, optional
        Band at which to generate the PB.

    Returns:
    --------
    pbcorimage : str
        Path to the PB-corrected CASA image.
    pbimage : str
        Path to the PB response CASA image itself (same coordinate system/per-plane beams as
        'inpimage', copied verbatim -- only pixel values are replaced)."""

    from katbeam import JimBeam

    ia = _image_tool()

    pbcorimage = inpimage.replace('.image', '.katbeam_pbcor.image')
    pbimage = inpimage.replace('.image', '.katbeam.pb')

    ia.open(inpimage)
    csys = ia.coordsys().torecord()
    imgdata = ia.getchunk()
    shape = ia.shape()

    cx, cy = shape[0]//2, shape[1]//2

    cdelt = np.abs(csys['direction0']['cdelt'][0])
    unit = csys['direction0']['units'][0]

    if unit == 'rad':
        cdelt = np.rad2deg(cdelt)
    elif unit == "'":
        cdelt /= 60.

    if pbband == 'LBand':
        PBeam = JimBeam('MKAT-AA-L-JIM-2020')
    elif pbband == 'SBand':
        PBeam = JimBeam('MKAT-AA-S-JIM-2020')
    elif pbband == 'UHF':
        PBeam = JimBeam('MKAT-AA-UHF-JIM-2020')
    else:
        logger.error('Input pbband not recognized. Must be one of LBand, SBand or UHF. Defaulting to LBand.')
        PBeam = JimBeam('MKAT-AA-L-JIM-2020')

    x = np.linspace(-cx, cx+1, shape[0])
    y = np.linspace(-cy, cy+1, shape[1])
    xx, yy = np.meshgrid(x, y)
    xx *= cdelt
    yy *= cdelt

    beam_I = np.empty(shape)
    for i in range(shape[-1]):
        beam_I[:,:,0,i] = PBeam.I(xx, yy, (ia.toworld([0,0,0,i])['numeric'][-1])/1e6)

    ia.close()

    pbcor_imgdata = imgdata/beam_I

    if pbthreshold > 0:
        pbcor_imgdata[beam_I < pbthreshold] = np.nan

    shutil.copytree(inpimage, pbimage)
    ia.open(pbimage)
    ia.putchunk(beam_I)
    ia.close()

    shutil.copytree(inpimage, pbcorimage)
    ia.open(pbcorimage)
    ia.putchunk(pbcor_imgdata)
    ia.close()

    return pbcorimage, pbimage


def finalize_stage(outimage, export_dir, rebin=False, rebin_factor=None, pb_correct=False,
                    pbthreshold=0, pbband='LBand'):

    """Post-process the final stage's image: optional rebin -> optional PB-correction ->
    export to 'export_dir' (mirrors the prototype's 'fincubes/' step, per REFACTOR_PLAN.md's
    Phase 6 addendum). PB-correction is intentionally sequenced *before* export but does not
    gate it -- the exported product is valid whether or not PB-correction ran, per user
    direction. When PB-correction is on, the PB response cube itself is also exported to FITS
    (alongside the PB-corrected image), so callers can post-process both -- see
    `fincubes_postprocess.py` (HI cube imaging's median-beam/frequency->velocity step, called
    by `hi_image.py` right after this function, not here -- this module stays CASA-generic,
    shared with continuum imaging, which doesn't want that step). Native frequency units are
    kept (no velocity conversion) here -- see `fincubes_postprocess.py`.

    Arguments:
    ----------
    outimage : str
        Path to the final stage's CASA image (from `run_stage()`).
    export_dir : str
        Directory to export the final FITS products into (created if missing).
    rebin : bool, optional
        Rebin the image before export?
    rebin_factor : list (of int), optional
        `imrebin()` factor, e.g. [2,2,1].
    pb_correct : bool, optional
        Apply katbeam PB-correction before export? Default False -- see REFACTOR_PLAN.md's
        Phase 6 addendum (the prototype always PB-corrected; this makes it opt-in).
    pbthreshold, pbband :
        Passed to `do_pb_corr()` when `pb_correct` is True.

    Returns:
    --------
    exported : str
        Path to the exported (FITS) image, for the final SoFiA pass to consume.
    pb_exported : str
        Path to the exported (FITS) PB response cube, or '' if `pb_correct` is False."""

    os.makedirs(export_dir, exist_ok=True)
    image_to_export = outimage
    pb_fitsimage = ''

    if rebin:
        rebinned = outimage.rstrip('/') + '_rebin.im'
        if not os.path.exists(rebinned):
            imrebin(imagename=outimage, outfile=rebinned, factor=rebin_factor, dropdeg=True, overwrite=True, crop=True)
        image_to_export = rebinned

    if pb_correct:
        image_to_export, pbimage = do_pb_corr(image_to_export, pbthreshold, pbband)
        pb_base = os.path.basename(pbimage.rstrip('/'))
        pb_fitsimage = os.path.join(export_dir, pb_base + '.fits')
        if not os.path.exists(pb_fitsimage):
            exportfits(imagename=pbimage, fitsimage=pb_fitsimage, overwrite=True, dropdeg=True, dropstokes=True)

    base = os.path.basename(image_to_export.rstrip('/'))
    fitsimage = os.path.join(export_dir, base + '.fits')
    if not os.path.exists(fitsimage):
        exportfits(imagename=image_to_export, fitsimage=fitsimage, overwrite=True, dropdeg=True, dropstokes=True)

    return fitsimage, pb_fitsimage
