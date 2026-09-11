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


def estimate_fitspec(spw_ranges, restfreq_mhz, target_velocity_kms, fitspw_vwidth_kms):

    """Build a CASA uvcontsub 'fitspec' string covering every SPW in 'spw_ranges' minus the
    line-exclusion window -- 'fitspw_vwidth_kms' (radio-convention km/s, total width, not
    half-width) centred on 'target_velocity_kms' around 'restfreq_mhz'.

    IMPORTANT, found the hard way (confirmed live: 'RuntimeError: Error trying to parse SPW:
    *:..., stoi'): unlike flagdata's 'spw' parameter (which badfreqranges' own multi-range
    '*:lo~hiMHz,*:lo~hiMHz' convention targets, see flag_round_1.py's do_pre_flag()),
    uvcontsub's 'fitspec' uses its own bespoke parser (UVContSubTVI::fitSpecToPerFieldMap) that
    does NOT accept the '*' wildcard for SPW ID -- every SPW must be named explicitly by its
    real integer ID (CASA's own inline help: "'17:100~500;600~910,19:7~100'"). A SPW omitted
    from the string is fit in full by fitspec's own documented default -- so this only ever
    needs to name SPWs that actually overlap the excluded line window; every other SPW is
    correctly handled by omission.

    This also means (a genuinely different problem from the wildcard syntax, not just a
    string-formatting fix): the caller MUST supply the *real* SPW structure of the MS
    uvcontsub will actually run on -- which, in this pipeline, only exists after
    partition.py/concat.py's per-SPW fan-out and re-concatenation, not the raw input MS's own
    (typically single-SPW) structure at '-B' time. Query it fresh via msmd against
    '[data] vis' at uvcontsub.py's own runtime, not in read_ms.py.

    Arguments:
    ----------
    spw_ranges : list
        (spwid, lo_mhz, hi_mhz) tuples for every SPW in the target MS (e.g. from
        msmd.chanfreqs(i) for i in range(msmd.nspw())).
    restfreq_mhz : float
        Rest frequency of the target line (MHz).
    target_velocity_kms : float
        Target's systemic velocity (radio convention, km/s) -- centre of the excluded window.
    fitspw_vwidth_kms : float
        Total velocity width (km/s) to exclude around target_velocity_kms.

    Returns:
    --------
    fitspec : str
        CASA uvcontsub 'fitspec' string (line window excluded from each overlapping SPW; SPWs
        with no overlap omitted, fit in full by fitspec's own default).

    Raises:
    -------
    ValueError
        If a SPW is entirely inside the excluded window (not expressible in fitspec's
        simple-string form -- would need the dictionary form's per-SPW 'NONE'/empty 'chan'),
        or if the excluded window doesn't overlap any SPW at all."""

    f_center = velocity_to_freq_radio(target_velocity_kms, restfreq_mhz)
    half_width_mhz = restfreq_mhz * (fitspw_vwidth_kms / 2.) / C_KM_S
    line_lo, line_hi = f_center - half_width_mhz, f_center + half_width_mhz

    parts = []
    for spwid, lo, hi in spw_ranges:
        if line_hi <= lo or line_lo >= hi:
            continue  #No overlap -- omit, fit this SPW in full (fitspec's own default).
        if line_lo <= lo and line_hi >= hi:
            raise ValueError(
                "SPW {0} ({1}-{2}MHz) is entirely inside the excluded line window "
                "({3}-{4}MHz) -- not expressible in fitspec's simple-string form. Narrow "
                "fitspw_vwidth or set [contsub] fitspw explicitly.".format(
                    spwid, round(lo, 4), round(hi, 4), round(line_lo, 4), round(line_hi, 4)))
        ranges = []
        if lo < line_lo:
            ranges.append('{0}~{1}MHz'.format(round(lo, 4), round(line_lo, 4)))
        if hi > line_hi:
            ranges.append('{0}~{1}MHz'.format(round(line_hi, 4), round(hi, 4)))
        parts.append('{0}:{1}'.format(spwid, ';'.join(ranges)))

    if not parts:
        raise ValueError(
            "[contsub] fitspw_vwidth={0}km/s around target_velocity={1}km/s doesn't overlap "
            "any SPW in {2} -- check target_velocity/restfreq.".format(
                fitspw_vwidth_kms, target_velocity_kms, spw_ranges))

    return ','.join(parts)
