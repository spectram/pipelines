#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Pure-python helpers for auto-estimating [contsub] fitspw from a target's systemic
velocity and a desired exclusion width -- no CASA imports, unit-testable standalone with
plain python3 (mirrors correlator_modes.py/selfcal_stages.py's CASA-free-module precedent).
All frequencies/velocities here are plain floats (MHz / km/s) -- unit-string parsing (e.g.
a config's '1420.406MHz') is the caller's job, via CASA's own quanta tool where available
(see read_ms.py), to keep this module free of any CASA dependency."""

#IAU/CODATA exact value, km/s.
C_KM_S = 299792.458


def freq_to_velocity_radio(freq_mhz, restfreq_mhz):

    """Convert a frequency to a systemic velocity via the radio velocity convention
    (v = c*(f0-f)/f0) -- exact/linear in frequency, the appropriate convention for
    HI/spectral-line work (unlike the optical convention, which isn't linear in frequency).

    Arguments:
    ----------
    freq_mhz : float
        Frequency (MHz) to convert.
    restfreq_mhz : float
        Rest frequency of the line (MHz).

    Returns:
    --------
    velocity_kms : float
        Radio-convention systemic velocity (km/s)."""

    return C_KM_S * (restfreq_mhz - freq_mhz) / restfreq_mhz


def velocity_to_freq_radio(velocity_kms, restfreq_mhz):

    """Inverse of freq_to_velocity_radio() -- convert a radio-convention velocity back to
    a frequency.

    Arguments:
    ----------
    velocity_kms : float
        Radio-convention velocity (km/s).
    restfreq_mhz : float
        Rest frequency of the line (MHz).

    Returns:
    --------
    freq_mhz : float
        Frequency (MHz)."""

    return restfreq_mhz * (1 - velocity_kms / C_KM_S)


def estimate_fitspw(band_lo_mhz, band_hi_mhz, restfreq_mhz, target_velocity_kms, fitspw_vwidth_kms):

    """Build an MSSelection fitspw string covering [band_lo_mhz, band_hi_mhz] minus the
    line-exclusion window -- 'fitspw_vwidth_kms' (radio-convention km/s, total width, not
    half-width) centred on 'target_velocity_kms' around 'restfreq_mhz'. Mirrors
    badfreqranges' own multi-range '*:lo~hiMHz,*:lo~hiMHz' convention (see
    flag_round_1.py's do_pre_flag()), so it reads the same way as every other
    exclude-this-frequency-range setting in this pipeline.

    Arguments:
    ----------
    band_lo_mhz : float
        Low edge (MHz) of the band available to fit as continuum.
    band_hi_mhz : float
        High edge (MHz) of the band available to fit as continuum.
    restfreq_mhz : float
        Rest frequency of the target line (MHz).
    target_velocity_kms : float
        Target's systemic velocity (radio convention, km/s) -- centre of the excluded window.
    fitspw_vwidth_kms : float
        Total velocity width (km/s) to exclude around target_velocity_kms.

    Returns:
    --------
    fitspw : str
        MSSelection string for the continuum-fit channels (the line window excluded).

    Raises:
    -------
    ValueError
        If the excluded window covers the entire band, leaving nothing to fit."""

    f_center = velocity_to_freq_radio(target_velocity_kms, restfreq_mhz)
    half_width_mhz = restfreq_mhz * (fitspw_vwidth_kms / 2.) / C_KM_S
    f_lo = max(f_center - half_width_mhz, band_lo_mhz)
    f_hi = min(f_center + half_width_mhz, band_hi_mhz)

    if f_lo <= band_lo_mhz and f_hi >= band_hi_mhz:
        raise ValueError(
            "[contsub] fitspw_vwidth={0}km/s around target_velocity={1}km/s excludes the "
            "entire fit band ({2}-{3}MHz) -- nothing left to fit as continuum. Narrow "
            "fitspw_vwidth or set [contsub] fitspw explicitly.".format(
                fitspw_vwidth_kms, target_velocity_kms, band_lo_mhz, band_hi_mhz))

    parts = []
    if f_lo > band_lo_mhz:
        parts.append('*:{0}~{1}MHz'.format(round(band_lo_mhz, 4), round(f_lo, 4)))
    if f_hi < band_hi_mhz:
        parts.append('*:{0}~{1}MHz'.format(round(f_hi, 4), round(band_hi_mhz, 4)))
    return ','.join(parts)
