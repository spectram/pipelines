#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Post-processing for the [hi_image] fincubes export -- collapses a cube's per-plane
restoring beams (CASA's CASAMBM convention) to a single common beam (the median across
planes) in the header, estimates SoFiA S+C spatial kernel sizes from that beam, and converts
the spectral axis from frequency to optical velocity centred on the cube's middle channel.
This is the "data processing" step image_engine.finalize_stage()'s/the SoFiA final-pass
template's docstrings previously noted as deferred during Phase 6 -- see REFACTOR_PLAN.md.

The three steps are deliberately split across two containers/scripts, not run together:
`add_median_beam()` runs first, in `hi_image.py` (CASA side, right after
`finalize_stage()`'s export) -- the final SoFiA pass must only run *after* this, both because
`estimate_spatial_kernels()` needs the collapsed BMAJ to be well-defined, and because SoFiA
itself is given the PB cube as `input.gain`. `estimate_spatial_kernels()` and
`freq_to_optical_velocity()` then run in `hi_sofia.py` (SoFiA container -- astropy is
available there too, confirmed), the kernel estimate feeding that same final SoFiA call's
`scfind.kernelsXY` override and the velocity conversion running only *after* SoFiA completes
(SoFiA itself still sees a frequency axis).

No CASA import in this module itself -- kept independent/unit-testable with plain FITS files.

Not wired to continuum imaging ([cont_image]/science_image.py): mfs continuum images are
single-plane (no per-channel BEAMS table to median, no meaningful spectral axis to convert),
so applying this there would either be a no-op or fail outright."""

import numpy as np
from astropy.io import fits

import logging
logger = logging.getLogger(__name__)

#Speed of light, m/s.
C_MS = 299792458.0


def add_median_beam(fitsfile):

    """Replace a cube's per-plane BEAMS table with a single common beam in the primary
    header, using the median BMAJ/BMIN/BPA across all planes. Overwrites 'fitsfile' in
    place. A no-op (logged, not an error) if 'fitsfile' has no BEAMS table already -- e.g.
    if it was exported with a single fixed restoringbeam rather than CASA's default
    per-plane beams.

    Arguments:
    ----------
    fitsfile : str
        Path to the FITS cube (as produced by image_engine.finalize_stage())."""

    with fits.open(fitsfile) as hdu:
        if len(hdu) < 2:
            logger.info("'{0}' has no per-plane BEAMS table -- leaving its header beam as-is.".format(fitsfile))
            return

        hdr = hdu[0].header
        beam_array = np.array([list(tup) for tup in hdu[1].data])
        hdr['BMAJ'] = np.median(beam_array[:, 0]) / 3600
        hdr['BMIN'] = np.median(beam_array[:, 1]) / 3600
        hdr['BPA'] = np.median(beam_array[:, 2])
        hdr.pop('CASAMBM', None)
        data = hdu[0].data

    nhdu = fits.PrimaryHDU(data=data, header=hdr)
    nhdu.writeto(fitsfile, overwrite=True)
    logger.info("Added median common beam (BMAJ={0:.6f} deg, BMIN={1:.6f} deg, BPA={2:.3f} deg) to '{3}'.".format(
        hdr['BMAJ'], hdr['BMIN'], hdr['BPA'], fitsfile))


def freq_to_optical_velocity(fitsfile, restfreq=None):

    """Convert 'fitsfile''s spectral (FREQ) axis to optical velocity (VOPT, m/s), referenced
    to the cube's central channel -- the new CRPIX is the middle channel (1-indexed FITS
    convention), with CRVAL/CDELT recomputed there and increasing/decreasing outwards from
    it. Matches the optical velocity convention already used for this cube's own
    tclean(veltype='optical') call (image_engine.run_stage()). Overwrites 'fitsfile' in
    place.

    Arguments:
    ----------
    fitsfile : str
        Path to the FITS cube.
    restfreq : float, optional
        Rest frequency in Hz. Defaults to the FITS header's own RESTFRQ (written by CASA's
        exportfits from tclean's own 'restfreq' parameter)."""

    with fits.open(fitsfile) as hdu:
        hdr = hdu[0].header

        freq_axis = None
        for i in range(1, hdr['NAXIS'] + 1):
            if hdr.get('CTYPE{0}'.format(i), '').upper().startswith('FREQ'):
                freq_axis = i
                break
        if freq_axis is None:
            raise ValueError("No FREQ axis found in '{0}'.".format(fitsfile))

        crval = hdr['CRVAL{0}'.format(freq_axis)]
        crpix = hdr['CRPIX{0}'.format(freq_axis)]
        cdelt = hdr['CDELT{0}'.format(freq_axis)]
        naxis = hdr['NAXIS{0}'.format(freq_axis)]

        f0 = restfreq if restfreq is not None else hdr['RESTFRQ']

        #Middle channel (1-indexed), proceeding outwards from there in both directions.
        center_pix = naxis // 2 + 1
        f_center = crval + (center_pix - crpix) * cdelt

        #Optical velocity convention: v = c*(f0-f)/f -- matches this cube's own
        #tclean(veltype='optical'). cdelt_vel is the local linear approximation at the
        #reference (centre) channel, i.e. dv/df evaluated there times the frequency
        #increment -- the standard simplification for a narrow (e.g. HI) band.
        v_center = C_MS * (f0 - f_center) / f_center
        cdelt_vel = -C_MS * f0 / f_center**2 * cdelt

        hdr['CTYPE{0}'.format(freq_axis)] = 'VOPT'
        hdr['CRVAL{0}'.format(freq_axis)] = v_center
        hdr['CRPIX{0}'.format(freq_axis)] = center_pix
        hdr['CDELT{0}'.format(freq_axis)] = cdelt_vel
        hdr['CUNIT{0}'.format(freq_axis)] = 'm/s'
        hdr['RESTFRQ'] = f0

        data = hdu[0].data

    nhdu = fits.PrimaryHDU(data=data, header=hdr)
    nhdu.writeto(fitsfile, overwrite=True)
    logger.info("Converted spectral axis to optical velocity (centred on channel {0}, v={1:.3f} km/s) in '{2}'.".format(
        center_pix, v_center / 1e3, fitsfile))


def estimate_spatial_kernels(fitsfile):

    """Estimate SoFiA S+C finder spatial smoothing kernel sizes (`scfind.kernelsXY`), in
    pixels, from the beam size relative to the pixel scale: no smoothing, half the beam
    width, and the full beam width. Must be called *after* `add_median_beam()` has collapsed
    the header to a single common BMAJ -- the whole point of running the final SoFiA pass
    only after the beam step (see `hi_sofia.py`) is so this estimate is well-defined.

    Arguments:
    ----------
    fitsfile : str
        Path to the FITS cube (already beam-collapsed).

    Returns:
    --------
    kernels : str
        Comma-separated kernel sizes in SoFiA's own list format, e.g. '0, 4, 8.2', ready to
        pass as a `scfind.kernelsXY` override."""

    with fits.open(fitsfile) as hdu:
        hdr = hdu[0].header
        cdelt1 = abs(hdr['CDELT1'])
        bmaj = hdr['BMAJ']

    beam_pix = bmaj / cdelt1
    kernels = [0, int(np.floor(beam_pix / 2)), round(beam_pix, 2)]
    logger.info("Estimated S+C spatial kernels from BMAJ={0:.6f} deg / |CDELT1|={1:.6f} deg/pix = {2:.2f} pix: {3}.".format(
        bmaj, cdelt1, beam_pix, kernels))

    return ', '.join(str(k) for k in kernels)
