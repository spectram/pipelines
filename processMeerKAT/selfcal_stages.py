#Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy
#See processMeerKAT.py for license details.

"""Self-calibration stage list, replacing the implicit `loop`/`nloops`-indexed parallel
arrays (`calmode`, `solint`, `niter`, `threshold`, ...) that used to live in
`bookkeeping.get_selfcal_params()`/`get_selfcal_args()` with one explicit, self-describing
record per loop -- see "Phase 2" in REFACTOR_PLAN.md for the full design rationale (the
motivating bug, the off-by-one `calmode[loop-1]`/`calmode[loop]` convention this replaces,
and the shipped 4-stage HI default).

This module is deliberately free of any CASA import (`casatools`/`casatasks`/`casampi`),
unlike `selfcal_part1.py`/`selfcal_part2.py` (which `from casatasks import *` at module
level and so cannot even be imported outside a CASA environment) and unlike
`bookkeeping.get_selfcal_args()` (which does a local `from casatools import
msmetadata,quanta`). Keeping the actual stage-parsing/resolution logic here, rather than
inline in those two, means it can be imported and unit-tested directly with a plain
`python3` interpreter -- no CASA install required. `bookkeeping.py` and
`selfcal_part1.py`/`selfcal_part2.py` should only ever call the functions below rather than
re-deriving any of this logic themselves.

A `[selfcal] stages` config value is a list of dicts, one per loop (including loop 0, the
initial dirty image), e.g. the shipped HI default:
```python
stages = [
    {'mask': None,   'apply_cal': None,   'derive_cal': '',   'niter': 10000,   'threshold': '0.5mJy'},
    {'mask': 'prev', 'apply_cal': None,   'derive_cal': 'p',  'niter': 50000,   'threshold': 10,  'solint': '1min'},
    {'mask': 'prev', 'apply_cal': 'prev', 'derive_cal': 'ap', 'niter': 80000,   'threshold': 5,   'solint': '10min'},
    {'mask': 'prev', 'apply_cal': 'prev', 'derive_cal': '',   'niter': 1000000, 'threshold': 3},
]
```
`nloops` is derived as `len(stages) - 1`, not separately configured. Extending the
self-cal chain is just appending one dict; no re-indexing of other arrays."""

from dataclasses import dataclass
from typing import Any, Optional

#Valid values for a stage's 'mask'/'apply_cal' relative references. None means "don't use
#one"; 'prev' means "use the thing the immediately preceding stage produced".
_VALID_REFS = (None, 'prev')


@dataclass(frozen=True)
class Stage:

    """One self-calibration loop's fully-resolved parameters. Replaces indexing into
    `calmode`/`solint`/`niter`/`threshold` (and the off-by-one `[loop-1]`/`[loop]`
    convention those arrays required) with one record per loop."""

    #Relative reference to the pixmask this stage's `tclean` call should use: None (no
    #mask) or 'prev' (the immediately preceding stage's pixmask). Was: bookkeeping's
    #`pixmask = imbase % (loop-1) + '.pixmask'` plus the separate blanking condition
    #`(loop == 0 and not os.path.exists(pixmask)) or (0 < loop < nloops and
    #calmode[loop] == '')` -- both replaced by making the choice an explicit, per-stage
    #config value instead of something inferred from position/calmode.
    mask: Optional[str] = None
    #Relative reference to the calibration table this stage should `applycal` before
    #imaging: None (skip) or 'prev' (apply the immediately preceding stage's
    #`derive_cal`, if it derived one). Was: `calmode[loop-1] != ''`.
    apply_cal: Optional[str] = None
    #gaincal `calmode` to solve for from this stage's image ('' to skip solving, 'p' for
    #phase-only, 'ap' for amplitude+phase). Was: `calmode[loop]`.
    derive_cal: str = ''
    #tclean `niter` for this stage. Was: `niter[loop]`.
    niter: int = 0
    #tclean/gaincal threshold for this stage: a S/N value if >= 1.0, otherwise a
    #CASA quantity string (e.g. '0.5mJy'). Was: `threshold[loop]`.
    threshold: Any = 0
    #gaincal `solint` for this stage, only meaningful when `derive_cal != ''`. Was:
    #`solint[loop]`.
    solint: str = ''


def parse_stages(raw_stages):

    """Parse a `[selfcal] stages` config value (as returned by
    `config_parser.parse_config()`, i.e. already `ast.literal_eval`'d into plain Python
    list/dict/str/... objects) into a list of `Stage`. Raises ValueError with a message
    naming the specific problem if `raw_stages` isn't a well-formed stage list; callers in
    a CASA context should catch this and `sys.exit(1)` after logging it, matching the
    pre-existing convention in `bookkeeping.get_selfcal_params()`.

    Arguments:
    ----------
    raw_stages : list (of dict)
        The raw '[selfcal] stages' config value.

    Returns:
    --------
    stages : list (of ``Stage``)"""

    if not isinstance(raw_stages, list) or len(raw_stages) == 0:
        raise ValueError("'stages' must be a non-empty list of stage dicts (one per self-cal loop, including loop 0), e.g. [{{'mask': None, 'apply_cal': None, 'derive_cal': '', 'niter': 10000, 'threshold': '0.5mJy'}}, ...]. Got: {0!r}".format(raw_stages))

    known_fields = set(Stage.__dataclass_fields__)
    stages = []
    for loop, raw in enumerate(raw_stages):
        if not isinstance(raw, dict):
            raise ValueError("'stages'[{0}] must be a dict, got {1}: {2!r}".format(loop, type(raw).__name__, raw))

        unknown = set(raw) - known_fields
        if unknown:
            raise ValueError("'stages'[{0}] has unknown key(s) {1}. Valid keys are {2}.".format(loop, sorted(unknown), sorted(known_fields)))

        stage = Stage(**raw)

        if stage.mask not in _VALID_REFS:
            raise ValueError("'stages'[{0}]['mask'] must be None or 'prev', got {1!r}.".format(loop, stage.mask))
        if stage.apply_cal not in _VALID_REFS:
            raise ValueError("'stages'[{0}]['apply_cal'] must be None or 'prev', got {1!r}.".format(loop, stage.apply_cal))
        if loop == 0 and (stage.mask == 'prev' or stage.apply_cal == 'prev'):
            raise ValueError("'stages'[0] (the initial, un-calibrated dirty image) cannot reference a previous stage ('prev') -- there isn't one.")

        stages.append(stage)

    return stages


def nloops(stages):

    """Number of self-calibration loops after the initial (loop 0) dirty image -- the
    direct replacement for the separately-configured 'nloops' value, now simply derived
    from the stage list's length.

    Arguments:
    ----------
    stages : list (of ``Stage``)

    Returns:
    --------
    nloops : int"""

    return len(stages) - 1


def resolve_mask(stages, loop, imbase):

    """Resolve the pixmask filename this loop's tclean/predict call should pass as
    `mask=`, from the stage's 'mask' relative reference. Was: bookkeeping's
    `pixmask = imbase % (loop-1) + '.pixmask'` (used unconditionally for the
    'tclean'/'predict' steps, then sometimes blanked back to '' by a separate condition --
    see `Stage.mask`'s docstring).

    Arguments:
    ----------
    stages : list (of ``Stage``)
    loop : int
        Current self-calibration loop.
    imbase : str
        `'<basename>_im_%d'`-style format string (as built in
        ``bookkeeping.get_selfcal_args()``); '%d' substituted with the loop number.

    Returns:
    --------
    pixmask : str
        Path to the previous stage's pixmask, or '' if this stage uses no mask."""

    if stages[loop].mask == 'prev':
        return imbase % (loop - 1) + '.pixmask'
    return ''


def should_apply_prev_cal(stages, loop):

    """Whether this loop should `applycal` the previous stage's derived calibration
    before imaging. Was: `1 <= loop <= nloops` and `calmode[loop-1] != ''`
    (`get_selfcal_args()`'s `prev_caltables` on-disk-existence check is a separate,
    unrelated safety check the caller should still apply in addition to this).

    Arguments:
    ----------
    stages : list (of ``Stage``)
    loop : int
        Current self-calibration loop.

    Returns:
    --------
    apply : bool"""

    return loop > 0 and stages[loop].apply_cal == 'prev' and stages[loop - 1].derive_cal != ''
