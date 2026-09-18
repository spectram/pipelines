#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

#!/usr/bin/env python3

"""Fresh '[hi_image] imspw'/SoFiA-kernel-linker defaults for a '--combine'd run -- the
M2-equivalent of read_ms.py's own '-B -H' block, but derived from real facts about the
combined data rather than copied wholesale from another track's own myconfig.txt (see
combine_tracks.write_combined_config()'s docstring for why that was wrong -- confirmed live,
2026-09-18).

Correlator mode itself is NOT re-identified here from the source MS's own spectral facts --
run_combine() already wrote '[run] correlator_mode' into 'args.config' before this runs, cross-
checked across every combined track's own already-recorded value (present on every track
unconditionally, unlike [hi_image] -- see correlator_modes.py's module docstring). Re-deriving
it here from source_vis's own ChanWid would be actively wrong: source_vis is each track's
'.contsub' output, already through partition.py's [crosscal] chanbin averaging, so its ChanWid
no longer matches identify_mode()'s native-channel-width table (confirmed live: 6.531kHz
observed vs the 3.265kHz '32K_NE107M' expects -- exactly the 2x chanbin=2 already baked in).

The only thing this script still needs live CASA/msmd access for is the source MS's own centre
frequency, and only when '[-F --centralspw]' wasn't given.

Invoked synchronously via os.system() from processMeerKAT.py's run_combine(), the same ad hoc
non-sbatch 'srun' pattern read_ms.py's own '-B' field extraction and
selfcal_scripts/set_sky_model.py's RACS query already use (see write_command()'s 'mpi'
argument docstring) -- not part of the generated sbatch DAG itself, since 'imspw' must already
be in the combined config before write_combine_jobs() builds that DAG."""

import os

import processMeerKAT
import config_parser
import correlator_modes

from casatasks import casalog
from casatools import msmetadata

logger = processMeerKAT.logger
msmd = msmetadata()


def main():

    args = processMeerKAT.parse_args()
    processMeerKAT.setup_logger(args.config, args.verbose)
    casalog.setlogfile('{0}.casa'.format(os.path.splitext(args.config)[0]))

    run_cfg = config_parser.parse_config(args.config)[0].get('run', {})
    mode = correlator_modes.get_mode_by_name(run_cfg.get('correlator_mode', ''))

    if args.centralspw is not None:
        centralfreq_mhz = args.centralspw
    else:
        #Only touch CASA/msmd at all when a centre frequency actually needs deriving -- channel
        #averaging (chanbin) doesn't shift the band centre, so, unlike ChanWid-based mode
        #identification, source_vis's own CtrFreq is still a valid real-data source for this.
        msmd.open(args.MS)
        _, _, _, _, ctrfreq_mhz = correlator_modes.get_spw_summary(msmd)
        msmd.done()
        centralfreq_mhz = ctrfreq_mhz
        logger.info("[-F --centralspw] not given -- using '{0}''s own centre frequency ({1:.3f}MHz).".format(args.MS, ctrfreq_mhz))

    imspw, sofia_overrides = correlator_modes.compute_imspw(mode, centralfreq_mhz)
    config_parser.overwrite_config(args.config, conf_dict={'imspw': imspw}, conf_sec='hi_image')
    logger.info("[hi_image] imspw={0} (correlator mode '{1}', centre frequency {2:.4f}MHz).".format(
        imspw, mode['name'] if mode is not None else '(unrecognized)', centralfreq_mhz))

    if sofia_overrides is not None:
        hi_image_cfg = config_parser.parse_config(args.config)[0].get('hi_image', {})
        for params_key in ('sofia_mask_params', 'sofia_final_params'):
            params = dict(hi_image_cfg.get(params_key, {}))
            params.update(sofia_overrides)
            config_parser.overwrite_config(args.config, conf_dict={params_key: repr(params)}, conf_sec='hi_image')
        logger.info("Defaulting SoFiA scfind.kernelsZ={0}, linker.radiusZ={1}, linker.minSizeZ={2} for correlator mode '{3}' (chanbin={4}).".format(
            mode['sofia_kernelsZ'], mode['sofia_linker_radiusZ'], mode['sofia_linker_minSizeZ'], mode['name'], mode['chanbin']))


if __name__ == '__main__':

    main()
