#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Registry of known MeerKAT correlator modes, identified from an input MS's own spectral window
metadata (the same Ch0(MHz)/ChanWid(kHz)/TotBW(kHz)/CtrFreq(MHz) facts CASA's `listobs` reports,
read here via `msmd` -- consistent with the rest of this pipeline, e.g. `read_ms.py`'s existing
`check_spw()` -- rather than parsing `listobs`'s text output).

Each `MODES` entry is this pipeline's whole accumulated knowledge of one correlator mode -- both
the HI-imaging-specific spectral defaults ([-H --hi_image] only: `chanbin`/`nspw`/`imspw_mhz`) and
per-script SLURM resource requests learned by actually profiling a run against this mode (`slurm`,
consumed by `slurm_config_registry.py`, applies regardless of -H -- e.g. `selfcal_part1` runs
whenever [-2 --do2GC] is set, independent of HI imaging). `read_ms.py` identifies the mode
unconditionally at '-B' time (every run, not just -H ones) and persists just the matched name into
'[run] correlator_mode'; `write_jobs()` looks it up again from there at '-R' time (no MS access
needed then) to resolve each script's resource request. A script/field with no data for the
identified mode -- or an unrecognized mode entirely -- falls back to today's plain defaults; there
is no hard-coded fallback value living in processMeerKAT.py itself.

Deliberately CASA-free (only `MODES` and pure functions) so it's unit-testable with plain
`python3`, mirroring `image_stages.py`/`selfcal_stages.py`.

Extend by adding another `MODES` entry -- one mode is shipped for now (32K_NE107M, the mode used
by HI_p1), per user direction to "add this one by one" as further modes are actually encountered.

**Identified by native channel width (kHz), not channel count or total bandwidth.** A delivered MS
is very often a spectral *subset* of a correlator mode's full native band -- e.g. HI_p1's own MS is
6127 channels over 20MHz, not the full 32768-channel/107MHz span '32K_NE107M' is named for, because
MeerKAT's SDP only archived a window around the target line. `nchan`/total bandwidth therefore vary
per-MS even within the same correlator mode; the one thing that doesn't is the mode's native
per-channel resolution (`totbw_MHz*1000/nchan` stays ~3.265kHz for 32K_NE107M regardless of how
much of the band was delivered) -- confirmed against HI_p1's real MS (ChanWid=3.265kHz exactly,
despite nchan=6127/TotBW=20.0MHz not matching (32768, 107) at all)."""

#Each entry: round(native channel width in kHz) -> per-mode pipeline knowledge.
#  chanbin   : [crosscal] chanbin default (only applied when [-H --hi_image] is set) -- mstransform
#              channel-averaging factor applied during partition.py (e.g. 2 halves the
#              correlator's native channel resolution).
#  nspw      : [crosscal] nspw default (only applied when [-H --hi_image] is set) -- number of SPWs
#              to split the (already-narrowed, see read_ms.py) crosscal spw window into for
#              per-SPW parallelism.
#  imspw_mhz : total width (MHz) of the [hi_image] imspw band read_ms.py centres on this run's
#              central frequency (user [-F --centralspw], else the MS's own CtrFreq) -- narrower
#              than the general GENERAL_IMSPW_MHZ fallback below, since a narrowband correlator
#              mode has no need for a wide imaging band. Only applied when [-H --hi_image] is set.
#  slurm     : {pipeline_role: {'nodes': N, 'ntasks_per_node': M}} -- per-script SLURM resource
#              overrides, applied regardless of -H/-I/-2. Only include a script here once its
#              resource need has actually been profiled against a real run in this mode -- see
#              e.g. selfcal_part1's entry below for the provenance a new one should match.
MODES = {
    3.265: {
        'name': '32K_NE107M',
        'chanbin': 2,
        'nspw': 4,
        'imspw_mhz': 6,
        #Profiled real safe operating point (HI_p1/NGC4064, 'selfcal-memory-scaling' investigation
        #notes): 1 node/16 tasks and 2 nodes/16 tasks-per-node both OOM'd -- tclean's MPI
        #parallelism only splits *visibility* data across ranks, each rank's *image-side* buffers
        #(PSF, weight density, multiscale/w-projection scratch) are replicated in full on every
        #rank, so ranks-per-node (not total node count) drives per-node memory pressure. 2 nodes/8
        #tasks-per-node succeeded across all 4 selfcal loops (0 through the final
        #niter=1,000,000 deep clean) at 199-207 GiB/node against a 230GB budget -- kept at that
        #measured ~10-13% headroom rather than shaved any tighter, for tolerance against a larger
        #or differently-configured MS than the one this was profiled on.
        'slurm': {
            'selfcal_part1': {'nodes': 2, 'ntasks_per_node': 8},
        },
    },
}

#Fallback [hi_image] imspw band width (MHz) for a correlator mode not in MODES (matches the width
#the pre-existing [-F --centralspw] mechanism already used for [crosscal] spw).
GENERAL_IMSPW_MHZ = 10

#Absolute tolerance (kHz) when matching a real MS's channel width against a MODES entry -- MODES
#keys are rounded to 3 decimal places, and a real MS's derived ChanWid (total bandwidth / nchan)
#can pick up a little more slack than that from non-uniform edge channels -- still tight enough to
#keep distinct modes (e.g. a hypothetical ~1.3kHz 32K_NE44M) well apart from 32K_NE107M's ~3.265kHz.
CHANWID_TOLERANCE_KHZ = 0.05


def get_spw_summary(msmd):

    """Read Ch0(MHz)/ChanWid(kHz)/TotBW(kHz)/CtrFreq(MHz) -- the same per-MS facts CASA's
    `listobs` reports -- from an already-open `msmd` tool, aggregated across all of the MS's own
    SPWs (typically one, for a raw MeerKAT SDP MS; matches `read_ms.py`'s own `check_spw()`
    assumption that SPWs 0..nspw-1 jointly cover the full observed band).

    Arguments:
    ----------
    msmd : casatools.msmetadata
        Already-open (`msmd.open(vis)`) metadata tool.

    Returns:
    --------
    nchan : int
        Total channel count, summed across all SPWs.
    ch0_mhz : float
        Frequency (MHz) of the first channel of the first SPW -- `listobs`'s 'Ch0(MHz)'.
    chanwid_khz : float
        Channel width (kHz), assumed uniform -- `listobs`'s 'ChanWid(kHz)'.
    totbw_mhz : float
        Total bandwidth (MHz), summed across all SPWs -- `listobs`'s 'TotBW(kHz)' (here in MHz).
    ctrfreq_mhz : float
        Centre frequency (MHz) -- mean of the first and last channel's frequency across all SPWs --
        `listobs`'s 'CtrFreq(MHz)'."""

    nspw = msmd.nspw()

    nchan = sum(msmd.nchan(spw) for spw in range(nspw))
    totbw_mhz = sum(msmd.bandwidths(spw) for spw in range(nspw)) / 1e6

    lowest = msmd.chanfreqs(0)[0]
    highest = msmd.chanfreqs(nspw - 1)[-1]
    ctrfreq_mhz = (lowest + highest) / 2 / 1e6

    ch0_mhz = lowest / 1e6
    chanwid_khz = (totbw_mhz * 1e3) / nchan

    return nchan, ch0_mhz, chanwid_khz, totbw_mhz, ctrfreq_mhz


def identify_mode(chanwid_khz):

    """Match `chanwid_khz` against `MODES`, tolerant of rounding (see `CHANWID_TOLERANCE_KHZ`).
    Deliberately doesn't take `nchan`/total bandwidth -- see this module's docstring for why those
    aren't reliable mode identifiers (a delivered MS is often a spectral subset of the mode's full
    native band).

    Arguments:
    ----------
    chanwid_khz : float
        Native channel width in kHz (from `get_spw_summary()`).

    Returns:
    --------
    mode : dict or None
        The matching `MODES` entry (including its 'name'), or None if unrecognized -- callers
        should fall back to the pipeline's pre-existing (non-mode-specific) defaults in that case."""

    for mode_chanwid, defaults in MODES.items():
        if abs(chanwid_khz - mode_chanwid) <= CHANWID_TOLERANCE_KHZ:
            return defaults

    return None


def get_mode_by_name(name):

    """Look up a `MODES` entry by its own 'name' field -- the counterpart to `identify_mode()`
    (which matches by (nchan, totbw_mhz)), for callers that only have the name persisted from an
    earlier `identify_mode()` call (e.g. `write_jobs()` at '-R' time, reading '[run]
    correlator_mode' back out of the config file -- no MS/msmd access at that point).

    Arguments:
    ----------
    name : str
        A `MODES` entry's 'name' value (e.g. '32K_NE107M'), or '' / any unrecognized string.

    Returns:
    --------
    mode : dict or None
        The matching `MODES` entry, or None if `name` is empty or doesn't match any entry."""

    for defaults in MODES.values():
        if defaults['name'] == name:
            return defaults

    return None
