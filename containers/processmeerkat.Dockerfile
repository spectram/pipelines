# processMeerKAT pipeline container: CASA + casampi + PyBDSF + SoFiA-2 + SoFiA-image-pipeline in one
# image, on Pawsey's Cray-MPICH-ABI-compatible MPICH base image.
#
# Build instructions: see the local-only CONTAINER_BUILD_LOCAL.md (or docs/containers.md in the
# packaged repository). In short: build with Docker or Podman on a machine where you have root or
# rootless subuid ranges (not on Setonix), save to a tarball, and convert to a SIF with
# `singularity build x.sif docker-archive://x.tar` (that step works unprivileged, on Setonix too).
#
# Replaces containers/spack-idia-fixed.def (kept for the record). Differences, and why:
#   - Base is quay.io/pawsey/mpich-base (recipe: github.com/PawseySC/pawsey-containers,
#     setonix/mpi/mpich-base). It already ships MPICH built with ch4:ofi (ABI-compatible with Cray
#     MPICH, swapped in at runtime by Setonix's singularity/*-mpi module) and mpi4py built from source
#     against it -- the fix the old recipe and the pipeline's external idianext_mpi4py override existed
#     to provide.
#   - No Spack and no source builds of casacore/python-casacore: Ubuntu 24.04's system Python is 3.12,
#     and casatools, python-casacore and bdsf all publish cp312 manylinux wheels. That removes the
#     casacore LD_LIBRARY_PATH shim and the Spack-OpenSSL LD_PRELOAD shim.
#   - SoFiA-2 and SIP are in this image, so one container serves every pipeline script (no separate
#     SoFiA container, no external sip_pkgs directory).
#   - CASA measures/runtime data are installed at build time and auto-update is off (via
#     /opt/casa/casasiteconfig.py). CASA 6.7 ignores ~/.casarc / .casarc, so the old recipe's file had no
#     effect and every run auto-updated ~/.casa/data.
#   - casarecipes (almapolhelpers) is NOT installed: CASA 6 no longer ships it. Polarisation
#     calibration (-P) needs the pipeline's vendored xyamb (release plan R5), not this image.
#
# Package versions are pinned to what the deployed idianext.sif (built 2026-05-24) runs, so results
# can be compared like for like, except casampi (0.6.0, fixes a parallel mfs tclean bug) and Python
# (3.12, from the base image; was 3.13).

ARG BASE_IMAGE=quay.io/pawsey/mpich-base:mpich3.4.3-ubuntu24.04
FROM ${BASE_IMAGE}

# SoFiA-2 commit that reports "2.6.7 (25-Feb-2025)" -- the version in the SoFiA container the
# pipeline uses today. Override with --build-arg SOFIA_REF=v2.7.1 for the latest release.
ARG SOFIA_REF=31eadabf2486e825de7020f8b8c2dfb444871c53
ARG CASA_VERSION=6.7.3.21
ARG IMAGE_VERSION=dev
ARG RECIPE_GIT_HASH=unknown

LABEL org.opencontainers.image.title="processMeerKAT" \
      org.opencontainers.image.description="MeerKAT calibration and HI/continuum imaging pipeline runtime: CASA ${CASA_VERSION}, casampi, PyBDSF, SoFiA-2, SoFiA-image-pipeline" \
      org.opencontainers.image.authors="Sriram Sankar (spectram)" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.revision="${RECIPE_GIT_HASH}" \
      org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV DEBIAN_FRONTEND=noninteractive

# System packages:
#   python3-venv          the venv below
#   wcslib-dev            SoFiA-2 (WCS support)
#   imagemagick           SIP's -m combined figures (IM6 provides `convert`; sip_postprocess accepts it)
#   ghostscript           EPS output (SoFiA reliability plots, matplotlib EPS)
#   xvfb + X/GL libs      casaplotms (plot_data.py, optional crosscal plots) needs a virtual display
#   libgfortran5 etc.     runtime libraries the casatools wheel expects from the OS
RUN set -eux; \
    apt-get update; \
    apt-get -y --no-install-recommends install \
        python3-venv \
        wcslib-dev \
        imagemagick \
        ghostscript \
        xvfb xauth \
        libfontconfig1 libfreetype6 libxrender1 libxi6 libsm6 libxext6 libxkbcommon-x11-0 \
        libgl1 libglib2.0-0 libdbus-1-3 libfuse2t64 \
        libgfortran5 libgomp1 \
        less \
    ; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Venv that can still see the base image's system site-packages, so its source-built mpi4py (linked
# against the image's MPICH) is used rather than a PyPI wheel with its own bundled MPI. The venv path
# matches idianext.sif's, so existing pipeline settings that name /opt/venv/bin/python3 keep working.
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH=/opt/venv/bin:${PATH} \
    VIRTUAL_ENV=/opt/venv \
    PYTHONNOUSERSITE=1 \
    MPLBACKEND=Agg \
    APPIMAGE_EXTRACT_AND_RUN=1 \
    CASA_DATA_DIR=/opt/casa/data

RUN set -eux; \
    python -m pip install --no-cache-dir --upgrade pip setuptools wheel; \
    python -m pip install --no-cache-dir \
        "casatools==${CASA_VERSION}" \
        "casatasks==${CASA_VERSION}" \
        "casaconfig==1.5.0" \
        "casampi==0.6.0" \
        "casaplotms==2.8.2" \
        "casaplotserver==2.0.3" \
        "numpy==2.4.4" \
        "scipy==1.17.1" \
        "astropy==7.2.0" \
        "matplotlib==3.10.8" \
        "pillow==12.1.1" \
        "python-casacore==3.7.1" \
        "bdsf==1.13.0.post2" \
        "sofia-image-pipeline==1.4.0" \
        "katbeam @ git+https://github.com/ska-sa/katbeam@5cc1e40b055f7495e4a6a7de4ee8b3a21ff99aaa" \
    ; \
    python -c "import mpi4py, sys; assert mpi4py.__file__.startswith('/usr/'), 'mpi4py must come from the base image, not PyPI: ' + mpi4py.__file__; print('mpi4py', mpi4py.__version__, mpi4py.__file__)"

# casampi only initialises MPI when OMPI_COMM_WORLD_RANK is set (an OpenMPI-only check). Setonix's
# srun launches through Cray PMI/PALS, which never sets it. Bridge it from PMI_RANK, else
# SLURM_PROCID, at interpreter start-up. A .pth line (not a /.singularity.d/env script, which a
# Docker build can't create, and not sitecustomize.py, which Ubuntu's own copy in the standard library
# directory would shadow) runs in every Python process of this venv, before casampi is imported.
RUN set -eux; \
    SITE=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"); \
    printf '%s\n' "import os; _r = os.environ.get('PMI_RANK') or os.environ.get('SLURM_PROCID'); _r is not None and 'OMPI_COMM_WORLD_RANK' not in os.environ and os.environ.__setitem__('OMPI_COMM_WORLD_RANK', _r)" \
        > "${SITE}/zz_ompi_rank_shim.pth"

# CASA runtime and measures data, fixed at build time; no auto-update at runtime (the image is
# read-only as a SIF, and reproducible runs should not change data underneath themselves).
# To refresh, rebuild the image. /opt/casa/casasiteconfig.py is the first place casaconfig 1.5 looks
# for a site config (casaconfig/config.py). A user's own ~/.casa/config.py is read after it and
# can override it.
RUN set -eux; \
    mkdir -p "${CASA_DATA_DIR}"; \
    python -c "import casaconfig; casaconfig.pull_data('${CASA_DATA_DIR}')"; \
    printf '%s\n' \
        "# Site-wide CASA configuration for the processMeerKAT container." \
        "measurespath = '${CASA_DATA_DIR}'" \
        "measures_auto_update = False" \
        "data_auto_update = False" \
        > /opt/casa/casasiteconfig.py; \
    chmod -R a+rX /opt/casa

# SoFiA-2, OpenMP build, pinned commit.
RUN set -eux; \
    git clone https://gitlab.com/SoFiA-Admin/SoFiA-2.git /opt/SoFiA-2; \
    cd /opt/SoFiA-2; \
    git checkout "${SOFIA_REF}"; \
    make OMP=true; \
    install -m 0755 sofia /usr/local/bin/sofia; \
    rm -rf /opt/SoFiA-2/.git

# Record exactly what is installed, and keep a copy of this recipe inside the image.
COPY processmeerkat.Dockerfile /opt/processmeerkat-container/processmeerkat.Dockerfile
RUN set -eux; \
    python -m pip freeze > /opt/processmeerkat-container/pip-freeze.txt; \
    sofia 2>&1 | head -n 5 > /opt/processmeerkat-container/sofia-version.txt || true; \
    echo "${IMAGE_VERSION} ${RECIPE_GIT_HASH}" > /opt/processmeerkat-container/version.txt

# Build-time smoke test: every import a pipeline script makes outside the pipeline's own modules.
# (MPI itself can only be tested under srun on Setonix -- see the build instructions.)
RUN set -eux; \
    python -c "import casatools, casatasks, casampi, casaplotms, casacore.images, bdsf, astropy, scipy, matplotlib, PIL, katbeam, sip; print('casatools', casatools.version_string())"; \
    python -c "from casatasks import tclean, mstransform, uvcontsub, flagdata; print('casatasks ok')"; \
    sofia 2>&1 | grep -q 'SoFiA'; \
    command -v sofia_image_pipeline; \
    command -v convert

CMD ["/bin/bash"]
