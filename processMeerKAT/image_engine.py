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
              outlierfile='', nmajor=-1):

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
    nmajor : int, optional
        Passed straight through to `tclean()` -- caps the number of major cycles *this call*
        will run (-1, the default, is CASA's own "no limit"). Confirmed live (N4064
        combined-track deep clean, 2026-09-15): once most/all channels converge, `tclean` has
        no stopping criterion for "nothing left to clean" -- each subsequent major cycle
        still fully re-grids the whole dataset (a `Reached cyclethreshold` no-op per channel)
        for zero benefit, indistinguishable from real progress until you inspect individual
        `SDAlgorithmBase::deconvolve` log lines. A finite cap turns that wasted tail into a
        graceful `tclean()` return (this function's caller still gets a usable `outimage`,
        just possibly short of full convergence) instead of a walltime SIGKILL with no output
        at all. Only meaningful on a stage whose real per-cycle cost is high enough that
        wasted cycles matter (e.g. this cube's deep-clean stage, not a fast mask pass).

    Returns:
    --------
    outimage : str
        Path to the produced CASA image (`.image`, or `.image.tt0` for `deconvolver='mtmfs'`)."""

    outimage = imagename + ('.image.tt0' if deconvolver == 'mtmfs' else '.image')

    if os.path.exists(outimage):
        logger.info('Image "{0}" already exists. Not overwriting, continuing to next step.'.format(outimage))
        return outimage

    #Reuse an existing PSF from a previous (e.g. interrupted) call against the same
    #imagename, rather than unconditionally recomputing it every time -- confirmed live
    #(2026-09-12): the w-projection convolution-function gridding step alone
    #(wprojplanes=128) took ~36 minutes for one HI cube stage, and is entirely unaffected by
    #niter/deconvolution progress (unlike '.model'/'.residual'), so redoing it on every
    #manual resume is pure waste. Per tclean's own docs: calcpsf=False assumes '.psf'
    #already exists, and (since calcres defaults to True here) also needs '.sumwt' present
    #"for normalization purposes". If the caller changed weighting/robust/wprojplanes/etc
    #since that PSF was made, delete '<imagename>.psf'/'.sumwt' first to force a fresh one --
    #same manual-cleanup convention as every other idempotency check in this pipeline (e.g.
    #uvcontsub.py/combine_tracks.py's own os.path.exists() guards).
    calcpsf = not (os.path.exists(imagename + '.psf') and os.path.exists(imagename + '.sumwt'))
    if not calcpsf:
        logger.info('Reusing existing "{0}.psf"/"{0}.sumwt" -- not recomputing the PSF.'.format(imagename))

    #Same idea, for the *deconvolution* state rather than the PSF: a walltime-killed call
    #leaves a real, valid '.model'/'.residual' behind (mid-major-cycle SLURM kills still
    #flush the underlying CASA tables) -- calcres=False resumes minor-cycle work from that
    #state directly, per tclean's own docs ("assume a .residual image already exists"),
    #instead of re-deriving the initial residual (a real gridding pass, not free) from
    #scratch. Only valid when BOTH exist -- '.model' alone (e.g. after a deliberate reset,
    #see calcpsf's own comment on forcing a fresh start) isn't enough for tclean's own
    #contract. Distinct from calcpsf's reuse: a walltime kill mid-run leaves the PSF *and*
    #residual/model all still valid; a deliberate reset (different mask/params) clears
    #'.model'/'.residual'/'.mask' but may deliberately keep '.psf'/'.sumwt' if the gridding
    #geometry itself is unchanged -- see this pipeline's hi_image.py resume history.
    calcres = not (os.path.exists(imagename + '.residual') and os.path.exists(imagename + '.model'))
    if not calcres:
        logger.info('Reusing existing "{0}.residual"/"{0}.model" -- resuming minor-cycle work, not restarting.'.format(imagename))

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

    #tclean itself refuses a fresh 'mask=' argument once '<imagename>.mask' already exists
    #from an earlier call against this same imagename -- confirmed live (2026-09-16,
    #RuntimeError: "Mask image ... exists, but a specific input mask ... has also been
    #supplied. Please either reset mask='' to reuse the existing mask, or delete
    #<imagename>.mask before restarting"). On a genuine resume (calcres=False -- real
    #minor-cycle progress being continued, see its own comment above) that '.mask' is
    #exactly the region mask this call's own 'mask' FITS/CASA-image was already imported
    #into on the original, interrupted call -- reuse it (mask='') rather than re-supplying
    #the same source again. A deliberate reset still clears '.mask' alongside
    #'.model'/'.residual' (see calcres's own comment), so this only fires for a true resume.
    if not calcres and os.path.exists(imagename + '.mask'):
        logger.info('Reusing existing "{0}.mask" -- not re-supplying mask="{1}".'.format(imagename, maskarg))
        maskarg = ''

    kwargs = dict(vis=vis, selectdata=False, datacolumn='corrected', imagename=imagename,
        imsize=imsize, cell=cell, stokes=stokes, gridder=gridder, specmode=specmode,
        wprojplanes=wprojplanes, deconvolver=deconvolver, restoration=True,
        weighting=weighting, robust=robust, niter=niter, scales=scales,
        restfreq=restfreq, uvtaper=uvtaper, spw=spw, threshold=threshold, nterms=nterms,
        calcpsf=calcpsf, calcres=calcres, nmajor=nmajor, mask=maskarg, usemask=usemask,
        pbcor=False, pblimit=-1, restoringbeam=restoringbeam, gain=0.1, parallel=True,
        outlierfile=outlierfile)

    #Cube-specific tclean kwargs. parallel=True (the kwargs default above) was disabled here for
    #cube mode by an older CASA MPI cube-imaging bug workaround (science_image.py's original
    #precedent); confirmed fixed on this container's CASA version by a real ~20h side-by-side
    #serial-vs-parallel HI cube imaging test (no MPI errors either run, ~2% wall-clock gap by the
    #time each ran a comparable number of channels) -- see HI_p1's test_serial/test_parallel.
    if specmode == 'cube':
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
