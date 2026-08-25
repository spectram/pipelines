#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Shared SoFiA-invocation engine behind `hi_image.py`/`science_image.py`'s masking and
final source-finding passes -- see "Phase 6" in REFACTOR_PLAN.md. `aux_scripts/run_sofia.py`
(a *different* SoFiA usage -- continuum-subtraction masking, not HI/continuum-imaging
masking; don't conflate the two) now imports `update_sofia_config()`/`run_sofia()` from
here directly rather than keeping its own near-duplicate copies, so both usages share one
parameter-file-patching/shell-out/return-code-checking implementation. `run_pass()` and the
template constants below remain specific to this module's own masking/final-pass mechanism,
factored out here rather than duplicated across two entry scripts (`hi_sofia.py` and
`cont_sofia.py`)."""

import os
import subprocess

import logging
logger = logging.getLogger(__name__)

#Ported from m2-image-scripts (origin/HI-dev) -- see REFACTOR_PLAN.md's Phase 6 write-up.
MASK_TEMPLATE = 'default_hi_sofmask_mask.txt'
FINAL_TEMPLATE = 'default_hi_sofmask_final.txt'


def parse_sofia_config(file_path):
    config = {}
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = map(str.strip, line.split('=', 1))
                if value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                elif value.isdigit():
                    value = int(value)
                config[key] = value
    return config


def update_sofia_config(file_path, updates):

    """Update specific keys in a SoFiA parameter file with new values.

    Arguments:
    ----------
    file_path : str
        Path to the SoFiA parameter file.
    updates : dict
        Keys to update -> new values."""

    with open(file_path, 'r') as file:
        lines = file.readlines()

    with open(file_path, 'w') as file:
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _ = map(str.strip, line.split('=', 1))
                if key in updates:
                    file.write(f"{key} = {updates[key]}\n")
                else:
                    file.write(f"{line}\n")
            else:
                file.write(f"{line}\n")


def run_sofia(paramfile):

    """Run SoFiA on 'paramfile', raising if it exits non-zero (confirmed via a real run:
    SoFiA can fail -- e.g. 'No negative sources found' on a too-shallow/too-small test
    image -- while exiting 0, which would otherwise let the pipeline silently continue into
    the next stage with no mask ever produced, only surfacing as a confusing failure later).
    `aux_scripts/run_sofia.py` (a different SoFiA usage -- continuum-subtraction masking)
    now calls this same function rather than keeping its own unchecked copy, so it gets
    this check too."""

    command = "sofia " + paramfile
    result = subprocess.run(command, shell=True, text=True, capture_output=False)
    if result.returncode != 0:
        raise RuntimeError("SoFiA exited with code {0} running '{1}'. See the log above for SoFiA's own error message.".format(result.returncode, paramfile))


def run_pass(script_dir, run_dir, is_final, input_fits, output_dir, mask_name):

    """Run one SoFiA pass (masking or final), copying the appropriate template into
    'run_dir' (once) and patching it for this call.

    Arguments:
    ----------
    script_dir : str
        Directory the template files (MASK_TEMPLATE/FINAL_TEMPLATE) live in
        (`processMeerKAT.SCRIPT_DIR`).
    run_dir : str
        Directory to copy/patch the parameter file into (typically the current combo's
        output directory).
    is_final : bool
        Final source-finding pass (True) or masking pass (False)?
    input_fits : str
        Path to the input FITS image for this pass.
    output_dir : str
        SoFiA 'output.directory' -- where the mask/catalog/etc get written.
    mask_name : str
        Base filename (no extension) SoFiA should derive its outputs from -- matches
        `aux_scripts/run_sofia.py`'s '{0}_mask.fits' convention when `output.filename` is
        left blank, so `image_stages.resolve_mask()` can find it."""

    template = FINAL_TEMPLATE if is_final else MASK_TEMPLATE
    paramfile = os.path.join(run_dir, 'sofmask_{0}.txt'.format('final' if is_final else 'mask'))

    if not os.path.exists(paramfile):
        with open(os.path.join(script_dir, template)) as src, open(paramfile, 'w') as dst:
            dst.write(src.read())

    updates = {
        'input.data': input_fits,
        'output.directory': output_dir,
        'output.filename': mask_name,
    }
    update_sofia_config(paramfile, updates)

    logger.info('Running SoFiA ({0} pass) on "{1}".'.format('final' if is_final else 'masking', input_fits))
    run_sofia(paramfile)
