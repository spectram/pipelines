"""Post-processing figures for HI imaging: run sofia-image-pipeline (SIP) on the final SoFiA
pass's catalogue, then -- when ImageMagick isn't available for SIP's own `-m` figure combination --
build the same combined per-source figure with Pillow.

Called by `hi_sofia.py` after the final SoFiA pass (SIP needs the catalogue, moment maps and
cubelets that pass writes). Entirely optional and non-fatal: a missing SIP install, no network for
the survey download, or a failed figure step logs a warning and lets the pipeline continue --
`run_sip_safe()` never raises.

CASA-free (like `image_stages.py`): SIP and Pillow are pure Python, and the SoFiA container
`hi_sofia.py` runs in already provides astropy/matplotlib/astroquery/pvextractor/Pillow -- only
the small `sip` package itself is added, via `sip_path` (a directory on PYTHONPATH,
created with `pip install --no-deps --target <dir> sofia-image-pipeline`), passed to a child
interpreter so this doesn't depend on how the container's own PYTHONPATH is set up."""

import glob
import os
import re
import shutil
import subprocess
import sys

import logging
logger = logging.getLogger(__name__)

#Sub-command run in a child interpreter: SIP's console-script entry point.
_SIP_ENTRY = "import sys; sys.argv[0] = 'sofia_image_pipeline'; from sip.image_pipeline import main; sys.exit(main())"

WHITE = (255, 255, 255, 255)
#SIP's own combined-image size limit (bytes), see sip/combine_images.py.
FILE_SIZE_LIMIT = 8e5


def find_imagemagick():

    """Path to an ImageMagick command SIP's `-m` can use, or None. Prefers IM7's `magick`; accepts
    `convert` only if it identifies itself as ImageMagick (other tools, e.g. Karma, also ship a
    `convert`).

    Returns:
    --------
    path : str or None"""

    magick = shutil.which('magick')
    if magick:
        return magick

    convert = shutil.which('convert')
    if convert:
        try:
            out = subprocess.run([convert, '--version'], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if 'ImageMagick' in out:
            return convert

    return None


def _sip_env(sip_path):

    env = dict(os.environ)
    if sip_path and os.path.isdir(sip_path):
        env['PYTHONPATH'] = sip_path + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def _surveys_reachable(env, timeout=45):

    """Whether astroquery's SkyView client can fetch its survey list within 'timeout' seconds --
    the very first request SIP's survey download makes. Checked up front because that request has no
    timeout of its own and, confirmed live on Setonix compute nodes, can block forever on the socket
    even though a plain HTTPS request to SkyView from the same node succeeds; waiting out a full SIP
    timeout before falling back to offline mode would waste the job's time."""

    try:
        result = subprocess.run([sys.executable, '-c', 'from astroquery.skyview import SkyView; SkyView.survey_dict'],
                                env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _sip_available(env):

    result = subprocess.run([sys.executable, '-c', 'import sip.image_pipeline'], env=env, capture_output=True)
    return result.returncode == 0


#Pillow port of sip/combine_images.py (SIP 1.4.0) ---------------------------------------------------

def _load(path):
    from PIL import Image
    return Image.open(path).convert('RGBA')


def _chop_left(im, n):
    return im.crop((n, 0, im.width, im.height))


def _chop_right(im, n):
    return im.crop((0, 0, im.width - n, im.height))


def _splice_left(im, n):
    from PIL import Image
    out = Image.new('RGBA', (im.width + n, im.height), WHITE)
    out.paste(im, (n, 0))
    return out


def _splice_right(im, n):
    from PIL import Image
    out = Image.new('RGBA', (im.width + n, im.height), WHITE)
    out.paste(im, (0, 0))
    return out


def _splice_bottom(im, n):
    from PIL import Image
    out = Image.new('RGBA', (im.width, im.height + n), WHITE)
    out.paste(im, (0, 0))
    return out


def _scale(im, factor):
    from PIL import Image
    return im.resize((round(im.width * factor), round(im.height * factor)), Image.LANCZOS)


def _append_h(ims):
    from PIL import Image
    out = Image.new('RGBA', (sum(i.width for i in ims), max(i.height for i in ims)), WHITE)
    x = 0
    for i in ims:
        out.paste(i, (x, 0))
        x += i.width
    return out


def _append_v(ims):
    from PIL import Image
    out = Image.new('RGBA', (max(i.width for i in ims), sum(i.height for i in ims)), WHITE)
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    return out


def combine_figures_pillow(figdir, base, src_id, has_freq=False):

    """Pillow equivalent of SIP's `-m` ImageMagick figure combination for one source: top row of
    mom0 (with the survey overlay, if one was made) | mom0 | SNR | mom1 | mom2, bottom row of the
    aperture spectrum | full-band spectrum | PV plots. Follows the same branches as
    sip/combine_images.py: with or without a survey image, with or without `pv_min`, and for a
    catalogue with ('freq' column) or without a frequency axis. Writes '<base>_<id>_combo.png'
    into 'figdir'; scaled down by SIP's own byte-ratio rule if it exceeds 800kB.

    Arguments:
    ----------
    figdir : str
        SIP's '<base>_figures' directory.
    base : str
        Catalogue basename without '_cat.xml' (e.g. 'stage1').
    src_id : int
        Source id.
    has_freq : bool, optional
        Whether the catalogue has a 'freq' column (a FREQ, rather than velocity, spectral axis).

    Returns:
    --------
    out_path : str"""

    p = lambda name: os.path.join(figdir, '{0}_{1}_{2}.png'.format(base, src_id, name))

    survey_imgs = sorted(glob.glob(p('mom0_*')))
    mom0, snr, mom1, mom2 = (_chop_left(_load(p(n)), 40) for n in ('mom0', 'snr', 'mom1', 'mom2'))
    first = _load(survey_imgs[0]) if survey_imgs else _load(p('mom0'))
    top = _splice_bottom(_append_h([first, mom0, snr, mom1, mom2] if survey_imgs else [first, snr, mom1, mom2]), 18)

    spec = _scale(_load(p('spec')), 1.33)
    specboth = _scale(_chop_left(_load(p('specboth')), 40), 1.33)

    if os.path.isfile(p('pv')):
        if os.path.isfile(p('pv_min')):
            if has_freq:
                pv_min = _chop_left(_load(p('pv_min')), 132)
                pv = _splice_right(_chop_right(_load(p('pv')), 128), 40)
            else:
                pv_min = _chop_left(_load(p('pv_min')), 40)
                pv = _load(p('pv'))
            bottom = _append_h([_splice_left(spec, 20), _splice_left(specboth, 20), _splice_left(pv, 20), pv_min])
        else:
            bottom = _append_h([_splice_left(spec, 20), _splice_left(specboth, 20), _splice_left(_load(p('pv')), 20)])
    else:
        bottom = _append_h([_splice_left(spec, 20), _splice_left(specboth, 20)])

    out_path = p('combo')
    combo = _append_v([top, bottom])
    combo.save(out_path)

    size = os.path.getsize(out_path)
    if size > FILE_SIZE_LIMIT:
        _scale(combo, FILE_SIZE_LIMIT / size).save(out_path)
    return out_path


def _combine_all_pillow(figdir, base, catalog):

    with open(catalog) as f:
        has_freq = 'name="freq"' in f.read()

    ids = sorted({int(m.group(1)) for m in (re.search(r'_(\d+)_mom0\.png$', f)
                                             for f in glob.glob(os.path.join(figdir, '{0}_*_mom0.png'.format(base)))) if m})
    made = []
    for src_id in ids:
        if os.path.exists(os.path.join(figdir, '{0}_{1}_combo.png'.format(base, src_id))):
            continue
        try:
            made.append(combine_figures_pillow(figdir, base, src_id, has_freq=has_freq))
        except (OSError, KeyError) as err:
            logger.warning('Could not combine the figures for source {0} ({1}: {2}) -- its individual figures are still in {3}.'.format(
                src_id, type(err).__name__, err, figdir))
    return made


def run_sip(catalog, original_fits, sip_path='', surveys='', timeout=1800):

    """Run SIP on 'catalog' (a SoFiA-2 XML catalogue), then make sure every source has a combined
    figure: SIP's own `-m` when ImageMagick is available, the Pillow port above when it isn't (or
    if `-m` didn't produce one). The survey overlay needs network access; if the survey run fails
    or times out it is retried once offline (`-s none`), which just omits the survey image.

    Arguments:
    ----------
    catalog : str
        Path to the catalogue, named '<base>_cat.xml' (SoFiA's output.filename convention).
    original_fits : str
        The cube SoFiA ran on (final export, with the common beam in its header) -- passed to
        SIP's `-o` for full-band spectra.
    sip_path : str, optional
        Directory containing the `sip` package, if it isn't already importable.
    surveys : str, optional
        SIP `-s` value (e.g. 'DSS2 Blue', 'none' for offline); '' leaves SIP's own default.
    timeout : int, optional
        Seconds allowed per SIP invocation.

    Returns:
    --------
    ok : bool
        Whether figures were produced."""

    catalog = os.path.abspath(catalog)
    original_fits = os.path.abspath(original_fits)
    workdir = os.path.dirname(catalog)
    base = re.sub(r'_cat\.xml$', '', os.path.basename(catalog))

    env = _sip_env(sip_path)
    if not _sip_available(env):
        logger.warning("sofia-image-pipeline isn't importable (sip_path={0!r}) -- skipping SIP figures.".format(sip_path))
        return False

    magick = find_imagemagick()
    logger.info('ImageMagick {0}.'.format("found at '{0}' -- using SIP's own -m".format(magick) if magick
                else 'not found -- will combine figures with Pillow instead'))

    def invoke(extra):
        args = ['-c', os.path.basename(catalog), '-o', os.path.relpath(original_fits, workdir), '-ow'] + extra
        if magick:
            args += ['-m', magick]
        logger.info('Running sofia_image_pipeline {0}'.format(' '.join(args)))
        try:
            return subprocess.run([sys.executable, '-c', _SIP_ENTRY] + args, cwd=workdir, env=env, timeout=timeout).returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning('sofia_image_pipeline timed out after {0}s.'.format(timeout))
            return False

    offline = surveys.lower() == 'none'
    if not offline and not _surveys_reachable(env):
        logger.warning("SkyView isn't responding from this node (astroquery's survey-list request didn't complete in time) -- running offline (-s none), without the survey overlay.")
        surveys, offline = 'none', True

    survey_args = ['-s', surveys] if surveys else []
    ok = invoke(survey_args)
    if not ok and not offline:
        logger.warning('sofia_image_pipeline failed with the survey overlay (no network access?) -- retrying offline (-s none).')
        ok = invoke(['-s', 'none'])
    if not ok:
        logger.warning('sofia_image_pipeline did not complete -- no SIP figures.')
        return False

    figdir = os.path.join(workdir, base + '_figures')
    if not os.path.isdir(figdir):
        logger.warning("SIP finished but '{0}' doesn't exist.".format(figdir))
        return False

    made = _combine_all_pillow(figdir, base, catalog)
    if made:
        logger.info('Combined figures with Pillow: {0}.'.format(', '.join(os.path.basename(m) for m in made)))
    return True


def run_sip_safe(*args, **kwargs):

    """`run_sip()` that never raises -- these figures are a convenience, and must not fail a
    pipeline step whose real outputs are already written."""

    try:
        return run_sip(*args, **kwargs)
    except Exception as err:
        logger.warning('SIP post-processing failed ({0}: {1}) -- continuing without figures.'.format(type(err).__name__, err))
        return False
