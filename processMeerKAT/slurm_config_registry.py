#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Resolves a script's per-script SLURM resource request. All actual profiled data lives in
`correlator_modes.py`'s `MODES` entries (each mode's own `slurm` dict, keyed by `pipeline_role`) --
this module holds no per-script numbers of its own, just the lookup: `write_jobs()` (in
processMeerKAT.py) reads '[run] correlator_mode' (a name string `read_ms.py` persisted at '-B'
time, from identifying the input MS's correlator mode -- see correlator_modes.py) and calls
`get_override()` for each script it's about to write an sbatch file for.

A script with no data for the identified mode -- or an unrecognized/missing mode entirely -- gets
an all-`None` `SlurmOverride`, i.e. no change: the caller falls back to the run's own configured
`[slurm] nodes`/`ntasks_per_node`, exactly like every script did before this mechanism existed.

Distinct from `script_registry.py`: that module declares *properties* driving how a script's job
gets built (`cpu_intensive`, `exclusive_node`, `long_partition`, ...); this module supplies actual
per-script *resource numbers*, sourced from `correlator_modes.py`, not hard-coded here."""

from dataclasses import dataclass
from typing import Optional

import correlator_modes


@dataclass(frozen=True)
class SlurmOverride:

    """A resource override for one script's `pipeline_role`. Any field left `None` falls back to
    the run's own configured `[slurm]` value -- this narrows specific fields, it doesn't replace
    the whole resource request (e.g. `mem`/`time`/`partition` stay at their configured value
    unless a future `correlator_modes.py` entry also supplies them)."""

    nodes: Optional[int] = None
    ntasks_per_node: Optional[int] = None


def get_override(pipeline_role, mode_name=''):

    """Resolve the SLURM resource override for one script's `pipeline_role`, under the given
    (already-identified) correlator mode name.

    Arguments:
    ----------
    pipeline_role : str or None
        `script_registry.ScriptProperties.pipeline_role` for the script being written.
    mode_name : str, optional
        This run's `[run] correlator_mode` value (from `correlator_modes.identify_mode()`, run by
        `read_ms.py` at '-B' time) -- '' (the default) or an unrecognized name both mean "no mode
        identified", same as a `pipeline_role` with no `slurm` entry in that mode.

    Returns:
    --------
    override : SlurmOverride
        All-`None` (no override -- caller keeps the configured `[slurm]` value) unless the
        identified mode has a `slurm` entry for this `pipeline_role`."""

    if pipeline_role is None or mode_name == '':
        return SlurmOverride()

    mode = correlator_modes.get_mode_by_name(mode_name)
    if mode is None:
        return SlurmOverride()

    resource = mode.get('slurm', {}).get(pipeline_role)
    if resource is None:
        return SlurmOverride()

    return SlurmOverride(**resource)
