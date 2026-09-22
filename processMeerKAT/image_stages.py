#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Imaging stage list shared by `hi_image.py` (HI cube imaging, `[hi_image]`) and
`science_image.py` (continuum imaging, `[cont_image]`) -- see "Phase 6" in
REFACTOR_PLAN.md for the full design rationale. Deliberately mirrors
`selfcal_stages.py`'s shape (a `Stage` dataclass, `parse_stages()`, relative 'prev'
references resolved positionally) rather than inventing a new convention, and is likewise
free of any CASA import so it can be unit-tested with a plain `python3` interpreter.

A `[hi_image]`/`[cont_image]` `stages` config value is a list of dicts, one per imaging
stage (e.g. a 2-stage dirty-then-final chain):
```python
stages = [
    {'mask': None,   'niter': 50000,   'threshold': '0.6mJy'},
    {'mask': 'prev', 'niter': 1500000, 'threshold': '0.24mJy'},
]
```
A stage's `mask` can also be `'auto-multithresh'`, letting CASA's own automasking algorithm
generate/refine the mask internally each major cycle instead of a user-supplied region mask
(useful for e.g. stage 0, which has no previous stage to reference via `'prev'`).
The last stage in the list is implicitly "final": after its `tclean`, the engine (see
`image_engine.py`) runs optional rebin -> optional PB-correction -> export, then a final
SoFiA source-finding pass -- see `is_final()`. Every non-final stage instead gets a plain
SoFiA masking pass feeding the next stage's `mask='prev'` -- see `resolve_mask()`."""

import os
import statistics
from dataclasses import dataclass
from typing import Any, Optional

#Valid values for a stage's 'mask' relative reference. None means "don't use one"; 'prev'
#means "use the SoFiA mask produced from the immediately preceding stage's image";
#'auto-multithresh' means "let tclean's own auto-multithresh algorithm generate/refine its
#mask internally each major cycle" (CASA's usemask='auto-multithresh' -- see
#image_engine.run_stage()), rather than a user-supplied region mask.
_VALID_REFS = (None, 'prev', 'auto-multithresh')


@dataclass(frozen=True)
class Stage:

    """One imaging stage's fully-resolved parameters."""

    #Relative reference to the mask this stage's `tclean` call should use: None (no mask),
    #'prev' (the SoFiA island mask produced from the immediately preceding stage's image),
    #or 'auto-multithresh' (CASA's own automasking, see `resolve_mask()`).
    mask: Optional[str] = None
    #tclean `niter` for this stage.
    niter: int = 0
    #tclean threshold for this stage: a S/N value if >= 1.0, otherwise a CASA quantity
    #string (e.g. '0.6mJy'). None or '' (key omitted, None, or an empty string -- the
    #config convention elsewhere for "unset") means "derive it from the previous stage's
    #SoFiA noise output" -- see `resolve_threshold()`; not valid for stage 0, which has no
    #previous stage.
    threshold: Any = None


def parse_stages(raw_stages):

    """Parse a `[hi_image]`/`[cont_image]` `stages` config value (as returned by
    `config_parser.parse_config()`, i.e. already `ast.literal_eval`'d into plain Python
    list/dict/str/... objects) into a list of `Stage`. Raises ValueError with a message
    naming the specific problem if `raw_stages` isn't a well-formed stage list; callers in
    a CASA context should catch this and `sys.exit(1)` after logging it, matching
    `selfcal_stages.parse_stages()`'s convention.

    Arguments:
    ----------
    raw_stages : list (of dict)
        The raw 'stages' config value.

    Returns:
    --------
    stages : list (of ``Stage``)"""

    if not isinstance(raw_stages, list) or len(raw_stages) == 0:
        raise ValueError("'stages' must be a non-empty list of stage dicts (one per imaging stage), e.g. [{{'mask': None, 'niter': 50000, 'threshold': '0.6mJy'}}, ...]. Got: {0!r}".format(raw_stages))

    known_fields = set(Stage.__dataclass_fields__)
    stages = []
    for stage_num, raw in enumerate(raw_stages):
        if not isinstance(raw, dict):
            raise ValueError("'stages'[{0}] must be a dict, got {1}: {2!r}".format(stage_num, type(raw).__name__, raw))

        unknown = set(raw) - known_fields
        if unknown:
            raise ValueError("'stages'[{0}] has unknown key(s) {1}. Valid keys are {2}.".format(stage_num, sorted(unknown), sorted(known_fields)))

        stage = Stage(**raw)

        if stage.mask not in _VALID_REFS:
            raise ValueError("'stages'[{0}]['mask'] must be None, 'prev', or 'auto-multithresh', got {1!r}.".format(stage_num, stage.mask))
        if stage_num == 0 and stage.mask == 'prev':
            raise ValueError("'stages'[0] (the initial, unmasked dirty image) cannot reference a previous stage ('prev') -- there isn't one.")
        if stage_num == 0 and stage.threshold in (None, ''):
            raise ValueError("'stages'[0] must set 'threshold' explicitly -- an undefined threshold is derived from the previous stage's SoFiA noise output, and stage 0 has no previous stage.")

        stages.append(stage)

    return stages


def nstages(stages):

    """Number of imaging stages.

    Arguments:
    ----------
    stages : list (of ``Stage``)

    Returns:
    --------
    nstages : int"""

    return len(stages)


def is_final(stages, stage):

    """Whether 'stage' is the last stage in the chain -- the one that gets optional
    rebin/PB-correction/export and the final SoFiA source-finding pass, rather than a plain
    masking pass feeding the next stage.

    Arguments:
    ----------
    stages : list (of ``Stage``)
    stage : int
        Current imaging stage index.

    Returns:
    --------
    final : bool"""

    return stage == len(stages) - 1


def resolve_mask(stages, stage, imagename_fn):

    """Resolve the mask filename this stage's `tclean` call should pass as `mask=`, from
    the stage's 'mask' relative reference. Mirrors `selfcal_stages.resolve_mask()`.

    Arguments:
    ----------
    stages : list (of ``Stage``)
    stage : int
        Current imaging stage index.
    imagename_fn : callable
        Given a stage index, returns that stage's base imagename (no extension) -- e.g.
        `hi_image.py`'s per-combo `lambda s: 'hi_combo{0}/stage{1}'.format(combo,s)`.

    Returns:
    --------
    mask : str
        Path to the previous stage's SoFiA island mask FITS file (matching
        `aux_scripts/run_sofia.py`'s `'{0}_mask.fits'.format(imagename)` naming
        convention), the literal string 'auto-multithresh' (not a real path --
        `image_engine.run_stage()` special-cases it into `usemask='auto-multithresh'`
        rather than importing it as a file), or '' if this stage uses no mask."""

    if stages[stage].mask == 'prev':
        return imagename_fn(stage - 1) + '_mask.fits'
    if stages[stage].mask == 'auto-multithresh':
        return 'auto-multithresh'
    return ''


def resolve_threshold(stages, stage, imagename_fn, factor=1.3):

    """Resolve the `tclean` threshold for this stage. A threshold set explicitly in the
    stage list is returned unchanged. An undefined one (None or '') is derived from the previous
    stage's SoFiA masking pass: 'factor' times the median of that pass's per-channel noise
    spectrum ('<previous imagename>_noise.txt', written because the shared SoFiA template
    sets `output.writeNoise = true`), as a CASA quantity string in mJy.

    The median is taken over non-zero channels only -- SoFiA writes a noise of exactly 0 for
    channels with no data (e.g. cube channels beyond the selected spw, see CLAUDE.md), which
    would otherwise drag the estimate down -- and the median rather than the mean keeps any
    channels containing bright line emission from inflating it. Assumes the image (and hence
    the noise file) is in Jy/beam, as `tclean` writes.

    Arguments:
    ----------
    stages : list (of ``Stage``)
    stage : int
        Current imaging stage index.
    imagename_fn : callable
        Given a stage index, returns that stage's base imagename (no extension) -- same
        callable `resolve_mask()` takes.
    factor : float, optional
        Multiple of the RMS to use as the threshold.

    Returns:
    --------
    threshold : str or number
        The explicit threshold, or the derived one as e.g. '0.296mJy'."""

    threshold = stages[stage].threshold
    if threshold not in (None, ''):
        return threshold

    noise_file = imagename_fn(stage - 1) + '_noise.txt'
    if not os.path.exists(noise_file):
        raise FileNotFoundError("'stages'[{0}] has no 'threshold', so it is derived from the previous stage's SoFiA noise output, but '{1}' doesn't exist. Set 'output.writeNoise = true' for the masking pass (default_hi_sofmask.txt does), or give this stage an explicit 'threshold'.".format(stage, noise_file))

    values = []
    with open(noise_file) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            value = float(line.split()[1])
            if value > 0:
                values.append(value)
    if len(values) == 0:
        raise ValueError("'{0}' has no non-zero noise values to derive 'stages'[{1}]['threshold'] from.".format(noise_file, stage))

    rms = statistics.median(values)
    return '{0:.4g}mJy'.format(factor * rms * 1e3)
