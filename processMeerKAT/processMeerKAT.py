#!/usr/bin/env python3

__version__ = '2.1'

license = """
    Process MeerKAT data via CASA MeasurementSet.
    Copyright (C) 2022 Inter-University Institute for Data Intensive Astronomy.
    support@ilifu.ac.za

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import os
import sys
import re
import math
from dataclasses import dataclass, field
import config_parser
import bookkeeping
import script_registry
from shutil import copyfile
from copy import deepcopy
import logging
from time import gmtime
from datetime import datetime
logging.Formatter.converter = gmtime
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)-15s %(levelname)s: %(message)s")

#Set global limits for current pawsey cluster configuration
TOTAL_NODES_LIMIT = 1592
#Setonix 'work'/'long' node topology (confirmed via `scontrol show node`): 2 sockets x 64
#cores/socket = 128 physical cores, ThreadsPerCore=2 -> 256 logical CPUs (the SLURM 'cpu'
#TRES unit). This was previously 64 (stale -- looks like an Ilifu-era value never updated
#for Setonix's much larger nodes), which meant e.g. selfcal_part1's --exclusive job only
#ever requested 8x8=64 of the node's 256 logical CPUs while paying for (and blocking other
#jobs from) the whole node. Set to the physical core count rather than the logical/SMT
#count: CASA/tclean's FFT- and gridding-heavy work is numerically bound, where
#oversubscribing hyperthreads rarely helps and can hurt, and Setonix's own --exclusive
#admission control (see write_sbatch()'s exclusive_node branch) is keyed to the physical
#core count too.
CPUS_PER_NODE_LIMIT = 128
NTASKS_PER_NODE_LIMIT = CPUS_PER_NODE_LIMIT
MEM_PER_NODE_GB_LIMIT = 230 #257568 MB
MEM_PER_NODE_GB_LIMIT_HIGHMEM = 1508 #1544192 MB

#Setonix's 'work'/'long' partitions share nodes between jobs: --mem is capped proportional to
#requested cores (~1840 MB/core) unless the whole node is reserved with --exclusive. Requesting
#more memory than this ratio allows (e.g. the full-node MEM_PER_NODE_GB_LIMIT above, with only a
#handful of cores) makes sbatch fail with "Requested node configuration is not available".
MEM_PER_CPU_MB_SHARED = 1840

#Sensible starting memory request for jobs sharing a node (most of the pipeline) rather than the
#full-node MEM_PER_NODE_GB_LIMIT (only selfcal_part1 needs the whole node -- see write_sbatch()).
#Users can raise [-m --mem]/[mem] in the config for scripts that need more; jobs whose own
#parallelism already earns more than this (e.g. imaging/flagging) aren't capped by it.
DEFAULT_MEM_GB = 32

#Set global values for paths and file names
THIS_PROG = __file__
SCRIPT_DIR = os.path.dirname(THIS_PROG)
LOG_DIR = 'logs'
CALIB_SCRIPTS_DIR = 'crosscal_scripts'
AUX_SCRIPTS_DIR = 'aux_scripts'
SELFCAL_SCRIPTS_DIR = 'selfcal_scripts'
CONFIG = 'default_config.txt'
TMP_CONFIG = '.config.tmp'
MASTER_SCRIPT = 'submit_pipeline.sh'
SPW_PREFIX = '*:'

#Set global values for field, crosscal and SLURM arguments copied to config file, and some of their default values
FIELDS_CONFIG_KEYS = ['fluxfield','bpassfield','phasecalfield','targetfields','extrafields']
CROSSCAL_CONFIG_KEYS = ['minbaselines','chanbin','width','timeavg','createmms','keepmms','spw','nspw','calcrefant','refant','standard','badants','badfreqranges']
#Phase 2 (Pawsey refactor): 'nloops'/'niter'/'threshold'/'calmode'/'solint' -- the keys that
#actually vary per self-cal loop -- were replaced by a single 'stages' list (one dict per
#loop, 'nloops' derived as len(stages)-1); see selfcal_stages.py and REFACTOR_PLAN.md's
#Phase 2 write-up. Every other key here stays a plain, non-broadcast scalar (or list, e.g.
#imsize=[6144,6144]) -- bookkeeping.get_selfcal_params() no longer replicates any of them
#to an 'nloops'+1-long list.
SELFCAL_CONFIG_KEYS = ['stages','loop','cell','robust','imsize','wprojplanes','uvrange','nterms','gridder','deconvolver','discard_nloops','gaintype','outlier_threshold','flag','outlier_radius', 'atrous_do','flag_maxsize_bm','scales','usermask','pb_correct','pbthreshold','pbband']
#Phase 6 (Pawsey refactor): '[image]' renamed '[cont_image]' and given the same
#'stages'-list shape as '[hi_image]' (replacing the old flat 'niter'/'threshold'/'mask'
#scalars) -- see image_stages.py and REFACTOR_PLAN.md's Phase 6 write-up.
CONT_IMAGE_CONFIG_KEYS = ['vis','stages','cell','imsize','robust','uvtaper','scales','gridder','wprojplanes','deconvolver','weighting','nterms','specmode','restfreq','restoringbeam','stokes','rebin','rebin_factor','pb_correct','pbthreshold','pbband','outlierfile','sofia_mask_params','sofia_final_params','combo','stage']
#New in Phase 6: HI cube imaging, images '[run] hi_contsub_vis' (uvcontsub.py's output) via
#a 'stages' list (image_stages.py) crossed with 'hi_combos' (one entry per robust/uvtaper
#weighting combination to image, each getting the full stage chain independently -- see
#REFACTOR_PLAN.md's Phase 6 addendum). 'restfreq'/'imspw' are '[hi_image]''s own keys (not
#read from '[cont_image]') so a '-H'-only run's imaging behaviour never depends on
#'[cont_image]''s contents at all -- uvcontsub.py's 'fitspw'/'fitorder' live in their own
#'[contsub]' section (shared by '-H' and standalone '--contsub', not HI-specific).
HI_IMAGE_CONFIG_KEYS = ['hi_combos','stages','cell','imsize','scales','gridder','wprojplanes','deconvolver','weighting','restfreq','imspw','rebin','rebin_factor','pb_correct','pbthreshold','pbband','sofia_mask_params','sofia_final_params','combo','stage']
SLURM_CONFIG_STR_KEYS = ['container','mpi_wrapper','partition','time','name','dependencies','exclude','account','reservation']
SLURM_CONFIG_KEYS = ['nodes','ntasks_per_node','mem','plane','submit','precal_scripts','postcal_scripts','scripts','verbose','modules'] + SLURM_CONFIG_STR_KEYS

#Phase 3 (Pawsey refactor): cluster hardware facts and named partitions, previously hardcoded
#module constants and inline 'work'/'long'/'HighMem'/'Devel' string literals scattered through
#write_sbatch()/write_jobs()/format_args(). Now a '[cluster]' config section (see
#default_config.txt), read via get_cluster_kwargs() below -- editable per-project rather than
#requiring a code change if e.g. a different Setonix reservation/partition layout is ever needed.
#DEFAULT_CLUSTER_KWARGS mirrors default_config.txt's '[cluster]' section and is used verbatim
#for any config predating this section (get_cluster_kwargs() falls back to it via
#config_parser.has_section() rather than hard-requiring '[cluster]'), so existing configs built
#before this change keep working unchanged.
CLUSTER_CONFIG_STR_KEYS = ['default_partition','long_partition','highmem_partition','devel_partition']
CLUSTER_CONFIG_KEYS = ['total_nodes_limit','cpus_per_node','mem_per_node_gb','mem_per_node_gb_highmem','mem_per_cpu_mb_shared','default_mem_gb'] + CLUSTER_CONFIG_STR_KEYS
DEFAULT_CLUSTER_KWARGS = {
    'total_nodes_limit': TOTAL_NODES_LIMIT,
    'cpus_per_node': CPUS_PER_NODE_LIMIT,
    'mem_per_node_gb': MEM_PER_NODE_GB_LIMIT,
    'mem_per_node_gb_highmem': MEM_PER_NODE_GB_LIMIT_HIGHMEM,
    'mem_per_cpu_mb_shared': MEM_PER_CPU_MB_SHARED,
    'default_mem_gb': DEFAULT_MEM_GB,
    'default_partition': 'work',
    'long_partition': 'long',
    'highmem_partition': 'HighMem',
    'devel_partition': 'Devel',
}
CONTAINER = '/software/projects/pawsey1164/ssankar/containers/idianext.sif'
SOFIA_CONTAINER = '/software/projects/pawsey1164/ssankar/containers/SoFiA-V2.6.7-2025-03-12.sif'

#idianext.sif's venv Python is linked against a spack-built OpenSSL newer than the container's
#base-OS OpenSSL; without LD_PRELOAD forcing the venv's OpenSSL to load first, `import ssl`
#(needed transitively by casatasks) fails with a symbol-version mismatch (glibc resolves
#libcrypto's SONAME once, from whatever loads it first).
#NOTE: the spack path below is specific to this build of idianext.sif and will need updating if
#the container is rebuilt (spack installs are hash-suffixed).
_IDIANEXT_OPENSSL_LIB = '/opt/spack/opt/spack/linux-zen2/openssl-3.4.1-kd6nwzlpilohkirzkrhioadlkvonnjkz/lib64'

#idianext.sif is missing mpi4py, which casampi (casatasks' MPI client/server layer, used by
#createmms=True and tclean(parallel=True)) requires to do real multi-task MPI parallelism.
#Without it, every srun-launched task independently falls back to believing it's the sole
#process, and multiple tasks race on the same output (e.g. FileExistsError). Fixed here without
#rebuilding the container: a source build of mpi4py (NOT the bundled manylinux wheel, which
#statically links its own MPI and never talks to Slurm's PMI/PALS) against idianext.sif's own
#dynamic MPICH, which resolves libmpi.so.12 through Cray's ABI-compatibility shim
#(lib-abi-mpich) at runtime.
#
#This same directory also holds casampi==0.6.0 (pip install --no-deps --target), overriding
#the container's pinned casampi==0.5.9: 0.5.9's MPICommandServer runs dispatched 'exec' commands
#via bare exec(code) with no explicit globals dict, so variables assigned by one dispatched
#command (e.g. continuum/mfs tclean's `toolsi = synthesisimager()`) only live in that single
#call's transient locals and vanish before the next dispatched command (`toolsi.selectdata(...)`)
#can see them -- NameError: name 'toolsi' is not defined, reproducible on every rank. This is a
#known CASA bug (CAS-14733, Python 3.13-specific) fixed upstream in casampi 0.6.0's
#`exec(code, globals())`. Only affects continuum/mfs parallel tclean (quick_tclean.py,
#selfcal_part1.py); createmms=True (partition.py) and cube-mode tclean use a different code path
#and were unaffected even under 0.5.9.
#
#See /software/projects/pawsey1164/ssankar/containers/idianext_mpi4py.
_IDIANEXT_MPI4PY_DIR = '/software/projects/pawsey1164/ssankar/containers/idianext_mpi4py'

#Phase 3 (Pawsey refactor): consolidates what were four separate container-keyed dicts
#(CONTAINER_PYTHON/CONTAINER_ENV/CONTAINER_BINDS/CONTAINER_PREPEND_ENV/CONTAINER_MODULES) into
#one registry of per-container overrides. Deliberately stays code, not user config (unlike
#[cluster] above): these are deep technical workarounds tied to one specific container build
#(e.g. a spack-hash-suffixed OpenSSL path, a source-built mpi4py living outside the repo) -- if
#[slurm] container became freely swappable via one of these fields living in a user config,
#these would silently stop applying to any other container the user points at.
@dataclass(frozen=True)
class ContainerProfile:
    #Some containers (e.g. idianext.sif) install casatasks/casatools into a venv that isn't on
    #PATH by default via `singularity exec`.
    python: str = 'python3'
    #Extra environment variables, passed to `singularity exec --env` (replaces, not appends).
    env: dict = field(default_factory=dict)
    #`singularity exec --bind` paths.
    binds: list = field(default_factory=list)
    #Vars to PREPEND to (not replace) via a host-side shell `export` inserted before the
    #singularity exec call, rather than via `singularity exec --env` -- see write_command().
    prepend_env: dict = field(default_factory=dict)
    #Override for the [slurm] modules list (normally the same singularity/4.1.0-mpi module for
    #every script). None means "use the configured [slurm] modules unchanged".
    modules: list = None

CONTAINER_PROFILES = {
    CONTAINER: ContainerProfile(
        python='/opt/venv/bin/python3',
        env={
            'LD_PRELOAD': '{0}/libcrypto.so.3:{0}/libssl.so.3'.format(_IDIANEXT_OPENSSL_LIB),
            #PYTHONPATH is otherwise set by the site's singularity module to just SCRIPT_DIR (so
            #`import config_parser` etc. work) -- must be preserved here, since --env replaces
            #rather than appends to the container's default.
            'PYTHONPATH': '{0}:{1}'.format(_IDIANEXT_MPI4PY_DIR, SCRIPT_DIR),
            #casampi's MPIEnvironment only attempts MPI initialisation if 'OMPI_COMM_WORLD_RANK'
            #is present in the environment (an OpenMPI-only check; Setonix's srun launches via
            #PMI/PALS, which never sets it). It's only checked for presence, not correctness, so
            #pass through the real per-task rank Slurm already provides via $PMI_RANK. This must
            #stay as the literal string '$PMI_RANK' (not expanded here) so each srun-launched
            #task substitutes its own value at runtime.
            'OMPI_COMM_WORLD_RANK': '$PMI_RANK',
        },
        #idianext.sif's MPI (via Cray's PALS launcher) needs to read its per-job rendezvous state
        #from /var/spool/slurmd on the compute node, which isn't bound into the container by the
        #site's default bind list -- without it, MPI_Init aborts.
        binds=['/var/spool/slurmd'],
        #The site's singularity module sets SINGULARITYENV_LD_LIBRARY_PATH to a long list of
        #Cray MPI/fabric library paths ending in a literal, unexpanded '$LD_LIBRARY_PATH'
        #(resolved by singularity at container-entry time, not by the calling shell) --
        #confirmed empirically that `--env LD_LIBRARY_PATH=...` does NOT compose with this
        #mechanism, it silently replaces it, dropping the Cray paths (a latent risk for
        #multi-node MPI, though not yet observed to break our current single-node jobs).
        #Exporting SINGULARITYENV_LD_LIBRARY_PATH ourselves beforehand, with our addition
        #prepended to the *current* value of that same host-side variable, preserves the
        #trailing '$LD_LIBRARY_PATH' token intact and correctly composes with the site's own
        #value. idianext.sif's python-casacore image-writing extension (casacore.images, used by
        #PyBDSF's CASA-format mask export in selfcal_part2.py) needs libcasa_python3.so.8/
        #libcasa_images.so.8/libcasa_casa.so.8 from the container's own casacore 3.7.1 build,
        #which isn't on the default library search path.
        prepend_env={'LD_LIBRARY_PATH': '/opt/casacore/lib'},
    ),
    #SOFIA_CONTAINER was built for Ilifu (Ubuntu 22.04); Setonix's "-mpi" module flavour
    #bind-mounts Cray fabric/Lustre host libraries (libcxi, liblustreapi, etc.) built against the
    #host's newer glibc (2.38) for MPI-enabled jobs, which the container's own older glibc can't
    #satisfy -- this breaks *every* dynamically linked binary in the container, not just
    #casampi/MPI-related ones (confirmed: even `echo` and `python3` failed with GLIBC_2.38 "not
    #found" errors). SoFiA is single-node/non-MPI and doesn't need those host libraries at all --
    #"-nohost" (no host-library injection) avoids pulling them in and the container runs cleanly.
    #Confirmed via a standalone test job: SoFiA ran successfully on a real continuum image (333
    #sources found, reliability ~1.0).
    SOFIA_CONTAINER: ContainerProfile(modules=['singularity/4.1.0-nohost']),
}

_WARNED_UNKNOWN_CONTAINERS = set()

def get_container_profile(container):

    """Look up a container's declared ContainerProfile by path. An unregistered container (e.g.
    a user swapping [slurm] container for a different build) gets an all-default
    ContainerProfile() -- logs a one-time warning per unique unrecognised container path, since
    that silently drops every Pawsey-specific fix (venv python, MPI env/binds, library paths)
    this registry exists for.

    Arguments:
    ----------
    container : str
        Path to singularity container.

    Returns:
    --------
    profile : class ``ContainerProfile``"""

    profile = CONTAINER_PROFILES.get(container)
    if profile is None:
        if container not in _WARNED_UNKNOWN_CONTAINERS:
            logger.warning("Container '{0}' isn't in the known CONTAINER_PROFILES registry and isn't the default -- "
                            "any Pawsey-specific container fixes (venv python, MPI env/binds, library paths) won't apply. "
                            "If this container needs its own fixes, add a ContainerProfile entry for it.".format(container))
            _WARNED_UNKNOWN_CONTAINERS.add(container)
        profile = ContainerProfile()
    return profile

MPI_WRAPPER = 'srun'
PRECAL_SCRIPTS = [('calc_refant.py',False,''),('partition.py',True,'')] #Scripts run before calibration at top level directory when nspw > 1
POSTCAL_SCRIPTS = [('concat.py',False,''),('plotcal_spw.py', False, ''),('selfcal_part1.py',True,''),('selfcal_part2.py',False,''), \
('run_sofia.py', False, SOFIA_CONTAINER), ('uvsub.py', False, ''), ('uvcontsub.py', True, ''), ('science_image.py', True, '')] #Scripts run after calibration at top level directory when nspw > 1
SCRIPTS = [ ('validate_input.py',False,''),
            ('flag_round_1.py',True,''),
            ('calc_refant.py',False,''),
            ('setjy.py',True,''),
            ('xx_yy_solve.py',False,''),
            ('xx_yy_apply.py',True,''),
            ('flag_round_2.py',True,''),
            ('xx_yy_solve.py',False,''),
            ('xx_yy_apply.py',True,''),
            ('split.py',True,''),
            ('quick_tclean.py',True,'')]


def check_path(path,update=False):

    """Check in specific location for a script or container, including in bash path, and in this pipeline's calibration
    scripts directory (SCRIPT_DIR/{CALIB_SCRIPTS_DIR,AUX_SCRIPTS_DIR}/). If path isn't found, raise IOError, otherwise return the path.

    Arguments:
    ----------
    path : str
        Check for script or container at this path.
    update : bool, optional
        Update the path according to where the file is found.

    Returns:
    --------
    path : str
        Path to script or container (if path found and update=True)."""

    newpath = path

    #Attempt to find path firstly in CWD, then directory up, then pipeline directories, then bash path.
    if os.path.exists(path) and path[0] != '/':
        newpath = '{0}/{1}'.format(os.getcwd(),path)
    if not os.path.exists(path) and path != '':
        if os.path.exists('../{0}'.format(path)):
            newpath = '../{0}'.format(path)
        elif os.path.exists('{0}/{1}'.format(SCRIPT_DIR,path)):
            newpath = '{0}/{1}'.format(SCRIPT_DIR,path)
        elif os.path.exists('{0}/{1}/{2}'.format(SCRIPT_DIR,CALIB_SCRIPTS_DIR,path)):
            newpath = '{0}/{1}/{2}'.format(SCRIPT_DIR,CALIB_SCRIPTS_DIR,path)
        elif os.path.exists('{0}/{1}/{2}'.format(SCRIPT_DIR,AUX_SCRIPTS_DIR,path)):
            newpath = '{0}/{1}/{2}'.format(SCRIPT_DIR,AUX_SCRIPTS_DIR,path)
        elif os.path.exists('{0}/{1}/{2}'.format(SCRIPT_DIR,SELFCAL_SCRIPTS_DIR,path)):
            newpath = '{0}/{1}/{2}'.format(SCRIPT_DIR,SELFCAL_SCRIPTS_DIR,path)
        elif os.path.exists(check_bash_path(path)):
            newpath = check_bash_path(path)
        else:
            #If it still doesn't exist, throw error
            raise IOError('File "{0}" not found.'.format(path))

    if update:
        return newpath
    else:
        return path

def check_bash_path(fname):

    """Check if file is in your bash path and executable (i.e. executable from command line), and prepend path to it if so.

    Arguments:
    ----------
    fname : str
        Filename to check.

    Returns:
    --------
    fname : str
        Potentially updated filename with absolute path prepended."""

    PATH = os.environ['PATH'].split(':')
    for path in PATH:
        if os.path.exists('{0}/{1}'.format(path,fname)):
            if not os.access('{0}/{1}'.format(path,fname), os.X_OK):
                raise IOError('"{0}" found in "{1}" but file is not executable.'.format(fname,path))
            else:
                fname = '{0}/{1}'.format(path,fname)
            break

    return fname

def parse_args():

    """Parse arguments into this script.

    Returns:
    --------
    args : class ``argparse.ArgumentParser``
        Known and validated arguments."""

    def parse_scripts(val):

        """Format individual arguments passed into a list for [ -S --scripts] argument, including paths and boolean values.

        Arguments/Returns:
        ------------------
        val : bool or str
            Path to script or container, or boolean representing whether that script is threadsafe (for MPI)."""

        if val.lower() in ('true','false'):
            return (val.lower() == 'true')
        else:
            return check_path(val)

    parser = argparse.ArgumentParser(prog=THIS_PROG,description='Process MeerKAT data via CASA MeasurementSet. Version: {0}'.format(__version__))

    parser.add_argument("-M","--MS",metavar="path", required=False, type=str, help="Path to MeasurementSet.")
    parser.add_argument("-C","--config",metavar="path", default=CONFIG, required=False, type=str, help="Relative (not absolute) path to config file.")
    parser.add_argument("-N","--nodes",metavar="num", required=False, type=int, default=1,
                        help="Use this number of nodes [default: 1; max: {0}].".format(TOTAL_NODES_LIMIT))
    parser.add_argument("-t","--ntasks-per-node", metavar="num", required=False, type=int, default=8,
                        help="Use this number of tasks (per node) [default: 16; max: {0}].".format(NTASKS_PER_NODE_LIMIT))
    parser.add_argument("-D","--plane", metavar="num", required=False, type=int, default=1,
                            help="Distribute tasks of this block size before moving onto next node [default: 1; max: ntasks-per-node].")
    parser.add_argument("-m","--mem", metavar="num", required=False, type=int, default=DEFAULT_MEM_GB,
                        help="Use this many GB of memory (per node) for threadsafe scripts [default: {0}; max: {1}].".format(DEFAULT_MEM_GB,MEM_PER_NODE_GB_LIMIT))
    parser.add_argument("-p","--partition", metavar="name", required=False, type=str, default=DEFAULT_CLUSTER_KWARGS['default_partition'], help="SLURM partition to use [default: 'Main'].")
    parser.add_argument("-T","--time", metavar="time", required=False, type=str, default="12:00:00", help="Time limit to use for all jobs, in the form d-hh:mm:ss [default: '12:00:00'].")
    parser.add_argument("-S","--scripts", action='append', nargs=3, metavar=('script','threadsafe','container'), required=False, type=parse_scripts, default=SCRIPTS,
                        help="Run pipeline with these scripts, in this order, using these containers (3rd value - empty string to default to [-c --container]). Is it threadsafe (2nd value)?")
    parser.add_argument("-b","--precal_scripts", action='append', nargs=3, metavar=('script','threadsafe','container'), required=False, type=parse_scripts, default=PRECAL_SCRIPTS, help="Same as [-S --scripts], but run before calibration.")
    parser.add_argument("-a","--postcal_scripts", action='append', nargs=3, metavar=('script','threadsafe','container'), required=False, type=parse_scripts, default=POSTCAL_SCRIPTS, help="Same as [-S --scripts], but run after calibration.")
    parser.add_argument("--modules", nargs='*', metavar='module', required=False, default=['singularity/4.1.0-mpi'], help="Load these modules within each sbatch script.")
    parser.add_argument("-w","--mpi_wrapper", metavar="path", required=False, type=str, default=MPI_WRAPPER,
                        help="Use this mpi wrapper when calling threadsafe scripts [default: '{0}'].".format(MPI_WRAPPER))
    parser.add_argument("-c","--container", metavar="path", required=False, type=str, default=CONTAINER, help="Use this container when calling scripts [default: '{0}'].".format(CONTAINER))
    parser.add_argument("-n","--name", metavar="unique", required=False, type=str, default='', help="Unique name to give this pipeline run (e.g. 'run1_'), appended to the start of all job names. [default: ''].")
    parser.add_argument("-d","--dependencies", metavar="list", required=False, type=str, default='', help="Comma-separated list (without spaces) of SLURM job dependencies (only used when nspw=1). [default: ''].")
    parser.add_argument("-e","--exclude", metavar="nodes", required=False, type=str, default='', help="SLURM worker nodes to exclude [default: ''].")
    parser.add_argument("-A","--account", metavar="group", required=False, type=str, default='', help="SLURM accounting group to use (e.g. 'b05-pipelines-ag' - check 'sacctmgr show user $USER cluster=ilifu-slurm20 -s format=account%%30')")
    parser.add_argument("-r","--reservation", metavar="name", required=False, type=str, default='', help="SLURM reservation to use. [default: ''].")

    parser.add_argument("-l","--local", action="store_true", required=False, default=False, help="Build config file locally (i.e. without calling srun) [default: False].")
    parser.add_argument("-s","--submit", action="store_true", required=False, default=False, help="Submit jobs immediately to SLURM queue [default: False].")
    parser.add_argument("-v","--verbose", action="store_true", required=False, default=False, help="Verbose output? [default: False].")
    parser.add_argument("-q","--quiet", action="store_true", required=False, default=False, help="Activate quiet mode, with suppressed output [default: False].")
    parser.add_argument("-P","--dopol", action="store_true", required=False, default=False, help="Perform polarization calibration in the pipeline [default: False].")
    parser.add_argument("-2","--do2GC", action="store_true", required=False, default=False, help="Perform (2GC) self-calibration in the pipeline [default: False].")
    parser.add_argument("-I","--science_image", action="store_true", required=False, default=False, help="Create a science image [default: False].")
    parser.add_argument("-H","--hi_image", action="store_true", required=False, default=False, help="Create an HI (spectral-line) cube image, independent of [-I --science_image] -- both can be set together [default: False].")
    parser.add_argument("--contsub", action="store_true", required=False, default=False, help="Run uvsub.py/uvcontsub.py to produce continuum-subtracted visibilities, independent of [-H --hi_image] (e.g. for contsub'd data without full cube imaging) [default: False].")
    parser.add_argument("-x","--nofields", action="store_true", required=False, default=False, help="Do not read the input MS to extract field IDs [default: False].")
    parser.add_argument("-j","--justrun", action="store_true", required=False, default=False, help="Just run the pipeline, don't rebuild each job script if it exists [default: False].")

    #add mutually exclusive group - don't want to build config, run pipeline, or display version at same time
    run_args = parser.add_mutually_exclusive_group(required=True)
    run_args.add_argument("-B","--build", action="store_true", required=False, default=False, help="Build config file using input MS.")
    run_args.add_argument("-R","--run", action="store_true", required=False, default=False, help="Run pipeline with input config file.")
    run_args.add_argument("-V","--version", action="store_true", required=False, default=False, help="Display the version of this pipeline and quit.")
    run_args.add_argument("-L","--license", action="store_true", required=False, default=False, help="Display this program's license and quit.")

    args, unknown = parser.parse_known_args()

    if len(unknown) > 0:
        parser.error('Unknown input argument(s) present - {0}'.format(unknown))

    if args.run:
        if args.config is None:
            parser.error("You must input a config file [--config] to run the pipeline.")
        if not os.path.exists(args.config):
            parser.error("Input config file '{0}' not found. Please set [-C --config] or write a new one with [-B --build].".format(args.config))

    #if user inputs a list a scripts, remove the default list
    if len(args.scripts) > len(SCRIPTS):
        [args.scripts.pop(0) for i in range(len(SCRIPTS))]
    if len(args.precal_scripts) > len(PRECAL_SCRIPTS):
        [args.precal_scripts.pop(0) for i in range(len(PRECAL_SCRIPTS))]
    if len(args.postcal_scripts) > len(POSTCAL_SCRIPTS):
        [args.postcal_scripts.pop(0) for i in range(len(POSTCAL_SCRIPTS))]

    #validate arguments before returning them
    validate_args(vars(args),args.config,parser=parser)
    return args

def raise_error(config,msg,parser=None):

    """Raise error with specified message, either as parser error (when option passed in via command line),
    or ValueError (when option passed in via config file).

    Arguments:
    ----------
    config : str
        Path to config file.
    msg : str
        Error message to display.
    parser : class ``argparse.ArgumentParser``, optional
        If this is input, parser error will be raised."""

    if parser is None:
        raise ValueError("Bad input found in '{0}' -- {1}".format(config,msg))
    else:
        parser.error(msg)

def validate_args(args,config,parser=None):

    """Validate arguments, coming from command line or config file. Raise relevant error (parser error or ValueError) if invalid argument found.

    Arguments:
    ----------
    args : dict
        Dictionary of slurm arguments from command line or config file.
    config : str
        Path to config file.
    parser : class ``argparse.ArgumentParser``, optional
        If this is input, parser error will be raised."""

    if parser is None or args['build']:
        if args['MS'] is None and not args['nofields']:
            msg = "You must input an MS [-M --MS] to build the config file."
            raise_error(config, msg, parser)

        if args['MS'] not in [None,'None'] and not os.path.isdir(args['MS']):
            msg = "Input MS '{0}' not found.".format(args['MS'])
            raise_error(config, msg, parser)

    if parser is not None and not args['build'] and args['MS']:
        msg = "Only input an MS [-M --MS] during [-B --build] step. Otherwise input is ignored."
        raise_error(config, msg, parser)

    if args['ntasks_per_node'] > NTASKS_PER_NODE_LIMIT:
        msg = "The number of tasks per node [-t --ntasks-per-node] must not exceed {0}. You input {1}.".format(NTASKS_PER_NODE_LIMIT,args['ntasks_per_node'])
        raise_error(config, msg, parser)

    if args['nodes'] > TOTAL_NODES_LIMIT:
        msg = "The number of nodes [-N --nodes] per node must not exceed {0}. You input {1}.".format(TOTAL_NODES_LIMIT,args['nodes'])
        raise_error(config, msg, parser)

    if args['mem'] > MEM_PER_NODE_GB_LIMIT:
        if args['partition'] != 'HighMem':
            msg = "The memory per node [-m --mem] must not exceed {0} (GB). You input {1} (GB).".format(MEM_PER_NODE_GB_LIMIT,args['mem'])
            raise_error(config, msg, parser)
        elif args['mem'] > MEM_PER_NODE_GB_LIMIT_HIGHMEM:
            msg = "The memory per node [-m --mem] must not exceed {0} (GB) when using 'HighMem' partition. You input {1} (GB).".format(MEM_PER_NODE_GB_LIMIT_HIGHMEM,args['mem'])
            raise_error(config, msg, parser)

    if args['plane'] > args['ntasks_per_node']:
        msg = "The value of [-P --plane] cannot be greater than the tasks per node [-t --ntasks-per-node] ({0}). You input {1}.".format(args['ntasks_per_node'],args['plane'])
        raise_error(config, msg, parser)

    # if args['account'] not in ['b03-idia-ag','b05-pipelines-ag']:
    #     from platform import node
    #     if 'slurm-login' in node() or 'slwrk' in node() or 'compute' in node():
    #         accounts=os.popen("for f in $(sacctmgr show user $USER --noheader cluster=ilifu-slurm20 -s format=account%30); do echo -n $f,; done").read()[:-1].split(',')
    #         if args['account'] not in accounts:
    #             msg = "Accounting group '{0}' not recognised. Please select one of the following from your groups: {1}.".format(args['account'],accounts)
    #             for account in accounts:
    #                 if args['account'] in account:
    #                     msg += ' Perhaps you meant accounting group "{0}".'.format(account)
    #                     break
    #             raise_error(config, msg, parser)
    #     else:
    #         msg = "Accounting group '{0}' not recognised. You're not using a SLURM node, so cannot query your accounts.".format(args['account'])
    #         raise_error(config, msg, parser)

    if args['reservation'] != '':
        from platform import node
        if 'slurm-login' in node() or 'slwrk' in node() or 'compute' in node():
            reservations=os.popen("scontrol show reservation | grep ReservationName | awk '{print $1}' | cut -d = -f2").read()[:-1].split('\n')
            if args['reservation'] not in reservations:
                msg = "Reservation '{0}' not recognised.".format(args['reservation'])
                if reservations == ['']:
                    msg += ' There are no active reservations.'
                else:
                     msg += ' Please select one of the following reservations, if applicable: {0}.'.format(reservations)
                raise_error(config, msg, parser)
        else:
            msg = "Reservation '{0}' not recognised. You're not using a SLURM node, so cannot query your accounts.".format(args['reservation'])
            raise_error(config, msg, parser)

def write_command(script,args,name='job',mpi_wrapper=MPI_WRAPPER,container=CONTAINER,\
                  casa_script=False,logfile=True,plot=False,SPWs='',nspw=1, cpus=1, mpi=True):

    """Write bash command to call a script (with args) directly with srun, or within sbatch file, optionally via CASA.

    Arguments:
    ----------
    script : str
        Path to script called (assumed to exist or be in PATH or calibration scripts directory).
    args : str
        Arguments to pass into script. Use '' for no arguments.
    name : str, optional
        Name of this job, to append to CASA output name.
    mpi_wrapper : str, optional
        MPI wrapper for this job. e.g. 'srun', 'mpirun', 'mpicasa' (may need to specify path).
    container : str, optional
        Path to singularity container used for this job.
    casa_script : bool, optional
        Is the script that is called within this job a CASA script?
    logfile : bool, optional
        Write the CASA output to a log file? Only used if casa_script==True.
    plot : bool, optional
        This job is a plotting task that needs to call xvfb-run.
    SPWs : str, optional
        Comma-separated list of spw ranges.
    nspw : int, optional
        Number of spectral windows.
    mpi : bool, optional
        Inject the container profile's MPI-triggering env vars (e.g. idianext.sif's
        'OMPI_COMM_WORLD_RANK', which makes casampi attempt MPI initialisation)? Default True,
        which is correct for every sbatch-generated job (multi-task 'threadsafe' scripts need it;
        single-task ones just get an inert rank-0 env var). Set False for the two ad hoc,
        non-sbatch 'srun' calls made directly from this script (read_ms.py's '-B' field
        extraction, set_sky_model.py's RACS query) -- neither is MPI-parallel, and outside an
        sbatch job's task allocation casampi's MPI_Init hangs/spins indefinitely instead of
        completing, since there's no real multi-task rendezvous for it to join (confirmed: two
        'read_ms.py' processes left spinning at ~95% CPU with no further progress after the
        script's own work was already done and logged).

    Returns:
    --------
    command : str
        Bash command to call with srun or within sbatch file."""

    arrayJob = ',' in SPWs and script_registry.get_properties(script).is_spw_fanout and nspw > 1

    #Store parameters passed into this function as dictionary, and add to it
    params = locals()
    params['LOG_DIR'] = LOG_DIR
    params['job'] = '${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}' if arrayJob else '${SLURM_JOB_ID}'
    params['job'] = '${SLURM_JOB_NAME}-' + params['job']
    params['casa_call'] = ''
    params['casa_log'] = '--nologfile'
    params['plot_call'] = ''
    command = ''

    params['script'] = check_path(script, update=True)

    #If specified by user, call script via CASA, call with xvfb-run, and write output to log file
    if plot:
        params['plot_call'] = 'xvfb-run -a'
    if logfile:
        params['casa_log'] = '--logfile {LOG_DIR}/{job}.casa'.format(**params)
    profile = get_container_profile(container)
    if casa_script:
        params['casa_call'] = "casa --nologger --nogui {casa_log} -c".format(**params)
    else:
        params['casa_call'] = profile.python

    env = profile.env if mpi else {k: v for k, v in profile.env.items() if k != 'OMPI_COMM_WORLD_RANK'}
    params['env_flags'] = ''.join(' --env {0}={1}'.format(k, v) for k, v in env.items())
    params['env_flags'] += ''.join(' --bind {0}'.format(b) for b in profile.binds)
    #Emitted as host-side `export`s (see ContainerProfile.prepend_env) rather than `--env`, so
    #they compose with (rather than clobber) any same-named SINGULARITYENV_* the site module sets.
    params['prepend_env'] = ''.join('export SINGULARITYENV_{0}="{1}:$SINGULARITYENV_{0}"\n'.format(k, v)
                                     for k, v in profile.prepend_env.items())

    if arrayJob:
        command += """#Iterate over SPWs in job array, launching one after the other
        SPWs="%s"
        arr=($SPWs)
        cd ${arr[SLURM_ARRAY_TASK_ID]}

        """ % SPWs.replace(',',' ').replace(SPW_PREFIX,'')

    command += "{prepend_env}{mpi_wrapper} -c {cpus} singularity exec{env_flags} {container} {plot_call} {casa_call} {script} {args}".format(**params)

    if arrayJob:
        command += '\ncd ..\n'

    return command


def write_sbatch(script,args,nodes=1,tasks=16,mem=DEFAULT_MEM_GB,name="job",runname='',plane=1,exclude='',mpi_wrapper=MPI_WRAPPER,container=CONTAINER,
                partition="work",time="12:00:00",casa_script=False,SPWs='',nspw=1,account='',reservation='',modules=[],justrun=False,cluster=None):

    """Write a SLURM sbatch file calling a certain script (and args) with a particular configuration.

    Arguments:
    ----------
    script : str
        Path to script called within sbatch file (assumed to exist or be in PATH or calibration directory).
    args : str
        Arguments passed into script called within this sbatch file. Use '' for no arguments.
    time : str, optional
        Time limit on this job.
    nodes : int, optional
        Number of nodes to use for this job.
    tasks : int, optional
        The number of tasks per node to use for this job.
    mem : int, optional
        The memory in GB (per node) to use for this job.
    name : str, optional
        Name for this job, used in naming the various output files.
    runname : str, optional
        Unique name to give this pipeline run, appended to the start of all job names.
    plane : int, optional
        Distrubute tasks for this job using this block size before moving onto next node.
    exclude : str, optional
        SLURM worker nodes to exclude.
    mpi_wrapper : str, optional
        MPI wrapper for this job. e.g. 'srun', 'mpirun', 'mpicasa' (may need to specify path).
    container : str, optional
        Path to singularity container used for this job.
    partition : str, optional
        SLURM partition to use (default: "Main").
    time : str, optional
        Time limit to use for this job, in the form d-hh:mm:ss.
    casa_script : bool, optional
        Is the script that is called within this job a CASA script?
    SPWs : str, optional
        Comma-separated list of spw ranges.
    nspw : int, optional
        Number of spectral windows.
    account : str, optional
        SLURM accounting group for sbatch jobs.
    reservation : str, optional
        SLURM reservation to use.
    modules : list, optional
        Modules to load upon execution of sbatch script.
    justrun : bool, optionall
        Just run the pipeline without rebuilding each job script (if it exists).
    cluster : dict, optional
        '[cluster]' config kwargs (see get_cluster_kwargs()) -- cluster hardware facts and named
        partitions. Defaults to DEFAULT_CLUSTER_KWARGS when not passed (e.g. a standalone call)."""

    if cluster is None:
        cluster = DEFAULT_CLUSTER_KWARGS

    if not os.path.exists(LOG_DIR):
        os.mkdir(LOG_DIR)

    #Store parameters passed into this function as dictionary, and add to it
    params = locals()
    params['LOG_DIR'] = LOG_DIR

    #Use multiple CPUs for tclean and partition scripts
    params['cpus'] = 1
    if script_registry.get_properties(script).cpu_intensive:
        cpus = int(cluster['cpus_per_node']/tasks)
        params['cpus'] = cpus

    #hard-code for 2/4 polarisations
    if script_registry.get_properties(script).is_spw_fanout:
        dopol = config_parser.get_key(TMP_CONFIG, 'run', 'dopol')
        if dopol and 4*tasks < cluster['cpus_per_node']:
            params['cpus'] = 4
        elif not dopol and params['cpus'] > 2:
            params['cpus'] = 2

    #selfcal_part1 does wide-field imaging with large cubes and high wprojplanes, and needs the
    #whole node's memory regardless of core count -- reserve the node outright with --exclusive.
    #Every other job runs on Setonix's shared partitions, where SLURM ties --mem to allocated
    #cores (~MEM_PER_CPU_MB_SHARED per core): requesting more memory than that ratio allows
    #(without --exclusive) makes sbatch reject the job outright with "Requested node
    #configuration is not available". Some scripts are hard-coded to a single task/cpu for
    #correctness (not thread-safe), but may still need substantial memory for CASA operations on
    #the whole MS -- so let the configured mem request pull cpus-per-task up (reserving otherwise-
    #idle cores purely to unlock proportional memory) rather than silently shrinking mem to fit
    #whatever cpu count a script's parallelism heuristic happened to pick.
    if script_registry.get_properties(script).exclusive_node:
        params['exclusive'] = '\n#SBATCH --exclusive'
        #Setonix's --exclusive admission control additionally requires ntasks-per-node to evenly
        #partition the node's physical cores (cluster['cpus_per_node']) --
        #confirmed empirically: --ntasks-per-node=9 (this pipeline's scan-count-driven default,
        #irrelevant to core topology) is rejected outright with "Requested node configuration is
        #not available" under --exclusive, while 8 (a power of two, divides 128 evenly) succeeds,
        #even though sbatch --test-only passes for either and never catches this. Round down to
        #the nearest power of two so this always binds regardless of the configured task count.
        tasks = 2 ** int(math.log2(max(1, tasks)))
        params['tasks'] = tasks
        params['cpus'] = int(cluster['cpus_per_node'] / tasks)
        if params['partition'] == cluster['highmem_partition']:
            params['mem'] = cluster['mem_per_node_gb_highmem']
        else:
            params['mem'] = cluster['mem_per_node_gb']
    else:
        params['exclusive'] = ''
        max_cpus_per_task = max(1, int(cluster['cpus_per_node'] / tasks))
        node_mem_cap_gb = cluster['mem_per_node_gb_highmem'] if params['partition'] == cluster['highmem_partition'] else cluster['mem_per_node_gb']
        #Whatever cpus-per-task the parallelism heuristic above picked (which may be driven by
        #something unrelated to memory, e.g. partition.py's polarisation count) may still be too
        #few to satisfy the configured mem request under Setonix's shared-node ratio -- reserve
        #whichever is larger: the heuristic's cpus, or enough (otherwise-idle) cores to unlock the
        #configured memory. Never shrinks cpus below what the heuristic already chose.
        mem_derived_cpus = math.ceil(min(params['mem'], node_mem_cap_gb) * 1024 / cluster['mem_per_cpu_mb_shared'] / tasks)
        params['cpus'] = min(max(params['cpus'], mem_derived_cpus), max_cpus_per_task)
        params['mem'] = min(node_mem_cap_gb, int(params['cpus'] * tasks * cluster['mem_per_cpu_mb_shared'] / 1024))

    #run_sofia.py is single-process/OpenMP-threaded (pipeline.threads in its SoFiA parameter
    #file), not CASA/MPI -- the generic mem-driven cpu reconciliation above pulls it up to
    #~18 cpus to unlock the configured [slurm] mem (32GB default), far more than SoFiA can
    #actually use (confirmed: a real run on a 6144x6144 continuum image used ~144MB and only
    #the 8 threads it was given). Fix cpus at 10 (matching a 10-thread SoFiA config) and cap
    #mem proportionally so this doesn't violate Setonix's shared-node mem/cpu ratio. Also
    #give it its own short walltime rather than the pipeline's general-purpose default --
    #source-finding on a single continuum image is a matter of seconds to minutes, not hours.
    if 'run_sofia' in script:
        params['cpus'] = 10
        params['mem'] = min(params['mem'], int(params['cpus'] * tasks * cluster['mem_per_cpu_mb_shared'] / 1024))
        params['time'] = '02:00:00'

    #Use xvfb for plotting scripts
    properties = script_registry.get_properties(script)
    plot = properties.plot
    if not properties.casa_invocation:
        casa_script = False

    #Limit number of concurrent jobs for partition so that no more than 200 CPUs used at once
    nconcurrent = int(200 / (params['nodes'] * params['tasks'] * params['cpus']))
    if nconcurrent > nspw:
        nconcurrent = nspw

    params['command'] = write_command(script,args,name=name,mpi_wrapper=mpi_wrapper,container=container,\
                                      casa_script=casa_script,plot=plot,SPWs=SPWs,nspw=nspw, cpus=params['cpus'])
    if properties.is_spw_fanout and ',' in SPWs and nspw > 1:
        params['ID'] = '%A_%a'
        params['array'] = '\n#SBATCH --array=0-{0}%{1}'.format(nspw-1,nconcurrent)
    else:
        params['ID'] = '%j'
        params['array'] = ''
    params['exclude'] = '\n#SBATCH --exclude={0}'.format(exclude) if exclude != '' else ''
    params['reservation'] = '\n#SBATCH --reservation={0}'.format(reservation) if reservation != '' else ''

    if properties.long_running:
        params['command'] = 'ulimit -n 16384\n' + params['command']
    if properties.long_partition:
        params['partition'] = cluster['long_partition']

    #Some containers need a different singularity module than the configured default (e.g.
    #SOFIA_CONTAINER needs "-nohost" instead of "-mpi" -- see ContainerProfile.modules above).
    container_modules = get_container_profile(container).modules
    if container_modules is not None:
        modules = container_modules
    params['modules'] = ''
    if len(modules) > 0:
        for module in modules:
            if len(module) > 0:
                params['modules'] += "module load {0}\n".format(module)

    contents = """#!/bin/bash{array}{exclude}{reservation}{exclusive}
    #SBATCH --account={account}
    #SBATCH --nodes={nodes}
    #SBATCH --ntasks-per-node={tasks}
    #SBATCH --cpus-per-task={cpus}
    #SBATCH --mem={mem}GB
    #SBATCH --job-name={runname}{name}
    #SBATCH --output={LOG_DIR}/%x-{ID}.out
    #SBATCH --error={LOG_DIR}/%x-{ID}.err
    #SBATCH --partition={partition}
    #SBATCH --time={time}

    export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
    export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
    {modules}

    {command}"""

    #insert arguments and remove whitespace
    contents = contents.format(**params).replace("    ","")

    #write sbatch file
    sbatch = '{0}.sbatch'.format(name)
    if justrun and os.path.exists(sbatch):
        logger.debug('sbatch file "{0}" exists. Not overwriting due to [-j --justrun] option.'.format(sbatch))
    else:
        config = open(sbatch,'w')
        config.write(contents)
        config.close()
        logger.debug('Wrote sbatch file "{0}"'.format(sbatch))

def expand_selfcal_loop_scripts(scripts,config,handle_run_sofia=False):

    """Replicate a configured selfcal_part1.sbatch/selfcal_part2.sbatch pair in a script
    list 'nloops' times (accounting for a nonzero starting loop), so a single
    [selfcal_part1.py, selfcal_part2.py] pair in the configured scripts list expands into a
    full self-cal loop chain. Shared by write_master() and write_spw_master() (previously
    two near-identical inline blocks).

    Arguments:
    ----------
    scripts : list (of str)
        List of '<script>.sbatch' filenames.
    config : str
        Path to config file.
    handle_run_sofia : bool, optional
        Also move a 'run_sofia.sbatch' immediately following the selfcal pair inside the
        replicated block, so it runs once per loop rather than only after the last one
        (write_master()'s behaviour; write_spw_master() does not do this).

    Returns:
    --------
    scripts : list (of str)
        The (possibly expanded) script list."""

    def has_role(s,role):
        return script_registry.get_properties(s).pipeline_role == role

    if not (config_parser.has_section(config,'selfcal') and any(has_role(s,'selfcal_part1') for s in scripts)
            and any(has_role(s,'selfcal_part2') for s in scripts)):
        return scripts

    start_loop = config_parser.get_key(config, 'selfcal', 'loop')
    #'nloops' is derived from the 'stages' list's length (Phase 2 of the Pawsey refactor --
    #see selfcal_stages.py), not read as its own config key any more.
    nloops = len(config_parser.get_key(config, 'selfcal', 'stages')) - 1
    selfcal_loops = nloops - start_loop
    part1_idx = next(i for i,s in enumerate(scripts) if has_role(s,'selfcal_part1'))
    part2_idx = next(i for i,s in enumerate(scripts) if has_role(s,'selfcal_part2'))

    #check that we're doing nloops in order, otherwise don't duplicate scripts
    if part2_idx != part1_idx + 1:
        return scripts

    part1_name, part2_name = scripts[part1_idx], scripts[part2_idx]
    init_scripts = scripts[:part2_idx+1]
    final_scripts = scripts[part2_idx+1:]
    init_scripts.extend([part1_name,part2_name]*(selfcal_loops-1))
    if handle_run_sofia and len(final_scripts) > 0 and has_role(final_scripts[0],'run_sofia'):
        sofia_name = final_scripts.pop(0)
        init_scripts.append(sofia_name)
        init_scripts.append(part1_name)
        init_scripts.append(part2_name)
    else:
        init_scripts.append(part1_name)
    return init_scripts + final_scripts

def _expand_stage_pair_scripts(scripts,config,section,image_role,sofia_role,ncombos=1):

    """Shared by `expand_hi_combo_scripts()`/`expand_cont_image_stage_scripts()`: replicate
    a configured `<image_role>.sbatch`/`<sofia_role>.sbatch` pair 'nstages * ncombos' times,
    accounting for already-progressed 'combo'/'stage' state -- the imaging equivalent of
    `expand_selfcal_loop_scripts()`. Unlike that function, every stage (including the last)
    needs the full pair (its own SoFiA pass, masking or final), so this is a flat replace
    rather than an 'initial + N more, +1 special-cased final' expansion.

    Arguments:
    ----------
    scripts : list (of str)
        List of '<script>.sbatch' filenames.
    config : str
        Path to config file.
    section : str
        Config section ('hi_image' or 'cont_image') holding 'stages'/'combo'/'stage' (and
        'hi_combos' for 'hi_image').
    image_role, sofia_role : str
        `pipeline_role` values identifying the imaging/SoFiA script pair.
    ncombos : int, optional
        Number of combos to cross with 'stages' (1 for 'cont_image', which has no combo
        axis).

    Returns:
    --------
    scripts : list (of str)
        The (possibly expanded) script list."""

    def has_role(s,role):
        return script_registry.get_properties(s).pipeline_role == role

    if not (config_parser.has_section(config,section) and any(has_role(s,image_role) for s in scripts)
            and any(has_role(s,sofia_role) for s in scripts)):
        return scripts

    nstages = len(config_parser.get_key(config, section, 'stages'))
    start_combo = config_parser.get_key(config, section, 'combo')
    start_stage = config_parser.get_key(config, section, 'stage')
    total_pairs = nstages * ncombos
    done_pairs = start_combo * nstages + start_stage
    remaining_pairs = max(1, total_pairs - done_pairs)

    image_idx = next(i for i,s in enumerate(scripts) if has_role(s,image_role))
    sofia_idx = next(i for i,s in enumerate(scripts) if has_role(s,sofia_role))

    #check that the pair is adjacent and in order, otherwise don't duplicate scripts
    if sofia_idx != image_idx + 1:
        return scripts

    image_name, sofia_name = scripts[image_idx], scripts[sofia_idx]
    init_scripts = scripts[:image_idx]
    final_scripts = scripts[sofia_idx+1:]
    return init_scripts + [image_name,sofia_name]*remaining_pairs + final_scripts

def expand_hi_combo_scripts(scripts,config):

    """Replicate a configured `hi_image.sbatch`/`hi_sofia.sbatch` pair `len(stages) *
    len(hi_combos)` times, so a single [hi_image.py, hi_sofia.py] pair in the configured
    scripts list expands into the full per-combo, per-stage HI imaging chain -- see
    REFACTOR_PLAN.md's Phase 6 addendum and `_expand_stage_pair_scripts()`.

    Arguments:
    ----------
    scripts : list (of str)
        List of '<script>.sbatch' filenames.
    config : str
        Path to config file.

    Returns:
    --------
    scripts : list (of str)
        The (possibly expanded) script list."""

    if not config_parser.has_section(config,'hi_image'):
        return scripts
    ncombos = len(config_parser.get_key(config, 'hi_image', 'hi_combos'))
    return _expand_stage_pair_scripts(scripts,config,'hi_image','hi_image','hi_sofia',ncombos=ncombos)

def expand_cont_image_stage_scripts(scripts,config):

    """Replicate a configured `science_image.sbatch`/`cont_sofia.sbatch` pair
    `len(stages)` times, the continuum-imaging equivalent of `expand_hi_combo_scripts()`
    (no combo axis -- see `_expand_stage_pair_scripts()`).

    Arguments:
    ----------
    scripts : list (of str)
        List of '<script>.sbatch' filenames.
    config : str
        Path to config file.

    Returns:
    --------
    scripts : list (of str)
        The (possibly expanded) script list."""

    return _expand_stage_pair_scripts(scripts,config,'cont_image','science_image','cont_sofia')

def write_spw_master(filename,config,SPWs,precal_scripts,postcal_scripts,submit,dir='jobScripts',pad_length=5,dependencies='',timestamp='',slurm_kwargs={}):

    """Write master master script, which separately calls each of the master scripts in each SPW directory.

    filename : str
        Name of master pipeline submission script.
    config : str
        Path to config file.
    SPWs : str
        Comma-separated list of spw ranges.
    precal_scripts : list, optional
        List of sbatch scripts to call in order, before running pipeline in SPW directories.
    postcal_scripts : list, optional
        List of sbatch scripts to call in order, after running pipeline in SPW directories.
    submit : bool, optional
        Submit jobs to SLURM queue immediately?
    dir : str, optional
        Name of directory to output ancillary job scripts.
    pad_length : int, optional
        Length to pad the SLURM sacct output columns.
    dependencies : str, optional
        Comma-separated list of SLURM job dependencies.
    timestamp : str, optional
        Timestamp to put on this run and related runs in SPW directories.
    slurm_kwargs : list, optional
        Parameters parsed from [slurm] section of config."""

    master = open(filename,'w')
    master.write('#!/bin/bash\n')
    SPWs = SPWs.replace(SPW_PREFIX,'')
    toplevel = len(precal_scripts + postcal_scripts) > 0

    scripts = precal_scripts[:]
    if len(scripts) > 0:
        command = 'sbatch'
        if dependencies != '':
            master.write('\n#Run after these dependencies\nDep={0}\n'.format(dependencies))
            command += " -d afterok:${Dep//,/:} --kill-on-invalid-dep=yes" #can also use sed 's/,/:/g' or tr , :
            dependencies = '' #Remove dependencies so it isn't fed into launching SPW scripts
        master.write('\n#{0}\n'.format(scripts[0]))
        master.write("allSPWIDs=$({0} {1} | cut -d ' ' -f4)\n".format(command,scripts[0]))
        scripts.pop(0)
    for script in scripts:
        command = "sbatch -d afterok:${allSPWIDs//,/:} --kill-on-invalid-dep=yes"
        master.write('\n#{0}\n'.format(script))
        master.write("allSPWIDs+=,$({0} {1} | cut -d ' ' -f4)\n".format(command,script))

    if any(script_registry.get_properties(s).pipeline_role == 'calc_refant' for s in precal_scripts):
        master.write('echo Calculating reference antenna, and copying result to SPW directories.\n')
    if any(script_registry.get_properties(s).is_spw_fanout for s in precal_scripts):
        master.write('echo Running partition job array, iterating over {0} SPWs.\n'.format(len(SPWs.split(','))))

    partition = len(precal_scripts) > 0 and script_registry.get_properties(precal_scripts[-1]).is_spw_fanout
    if partition:
        master.write('\npartitionID=$(echo $allSPWIDs | cut -d , -f{0})\n'.format(len(precal_scripts)))

    #Add time as extn to this pipeline run, to give unique filenames
    killScript = 'killJobs'
    summaryScript = 'summary'
    fullSummaryScript = 'fullSummary'
    errorScript = 'findErrors'
    timingScript = 'displayTimes'
    cleanupScript = 'cleanup'

    master.write('\n#Add time as extn to this pipeline run, to give unique filenames')
    master.write("\nDATE={0}\n".format(timestamp))
    master.write('mkdir -p {0}\n'.format(dir))
    master.write('mkdir -p {0}\n\n'.format(LOG_DIR))
    extn = '_$DATE.sh'

    for i,spw in enumerate(SPWs.split(',')):
        master.write('echo Running pipeline in directory "{1}" for spectral window {0}{1}\n'.format(SPW_PREFIX, spw))
        master.write('cd {0}\n'.format(spw))
        master.write('output=$({0} --config ./{1} --run --submit --quiet --justrun'.format(os.path.split(THIS_PROG)[1],config))
        if partition:
            master.write(' --dependencies=$partitionID\_{0}'.format(i))
        elif len(precal_scripts) > 0:
            master.write(' --dependencies=$allSPWIDs')
        elif dependencies != '':
            master.write(' --dependencies={0}'.format(dependencies))
        master.write(')\necho -e $output\n')
        if i == 0:
            master.write("IDs=$(echo $output | sed 's/.*IDs\:\s\(.*\)/\\1/')")
        else:
            master.write("IDs+=,$(echo $output | sed 's/.*IDs\:\s\(.*\)/\\1/')")
        master.write('\ncd ..\n\n')

    if any(script_registry.get_properties(s).pipeline_role == 'concat' for s in postcal_scripts):
        master.write('echo Will concatenate MSs/MMSs and create quick-look continuum cube across all SPWs for all fields from \"{0}\".\n'.format(config))
    scripts = expand_selfcal_loop_scripts(postcal_scripts[:], config)
    scripts = expand_hi_combo_scripts(scripts, config)
    scripts = expand_cont_image_stage_scripts(scripts, config)

    if len(scripts) > 0:
        command = "sbatch -d afterany:${IDs//,/:}"
        master.write('\n#{0}\n'.format(scripts[0]))
        if len(precal_scripts) == 0:
            master.write("allSPWIDs=$({0} {1} | cut -d ' ' -f4)\n".format(command,scripts[0]))
        else:
            master.write("allSPWIDs+=,$({0} {1} | cut -d ' ' -f4)\n".format(command,scripts[0]))
        scripts.pop(0)
        for script in scripts:
            command = "sbatch -d afterok:${allSPWIDs//,/:} --kill-on-invalid-dep=yes"
            master.write('\n#{0}\n'.format(script))
            master.write("allSPWIDs+=,$({0} {1} | cut -d ' ' -f4)\n".format(command,script))
    master.write('\necho Submitted the following jobIDs within the {0} SPW directories: $IDs\n'.format(len(SPWs.split(','))))

    prefix = ''
    #Write bash job scripts for the jobs run in this top level directory
    if toplevel:
        master.write('\necho Submitted the following jobIDs over all SPWs: $allSPWIDs\n')
        master.write('\necho For jobs over all SPWs:\n')
        prefix = 'allSPW_'
        write_all_bash_jobs_scripts(master,extn,IDs='allSPWIDs',dir=dir,prefix=prefix,pad_length=pad_length,slurm_kwargs=slurm_kwargs,devel_partition=get_cluster_kwargs(config)['devel_partition'])
        master.write('\nln -f -s {1}{2}{3} {0}/{1}{4}{3}\n'.format(dir,prefix,summaryScript,extn,fullSummaryScript))

    master.write('\necho For all jobs within the {0} SPW directories:\n'.format(len(SPWs.split(','))))
    header = '-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------' + '-'*pad_length
    do = """echo "for f in {%s,}; do if [ -d \$f ]; then cd \$f; ./%s/%s%s; cd ..; else echo Directory \$f doesn\\'t exist; fi; done;%s"""
    suffix = '' if toplevel else ' \"'
    write_bash_job_script(master, killScript, extn, do % (SPWs,dir,killScript,extn,suffix), 'kill all the jobs', dir=dir,prefix=prefix)
    write_bash_job_script(master, cleanupScript, extn, do % (SPWs,dir,cleanupScript,extn,' \"'), 'remove the MMSs/MSs within SPW directories \(after pipeline has run\), while leaving any concatenated data at the top level', dir=dir)

    do = """echo "counter=1; for f in {%s,}; do echo -n SPW \#\$counter:; echo -n ' '; if [ -d \$f ]; then cd \$f; pwd; ./%s/%s%s %s; cd ..; else echo Directory \$f doesn\\'t exist; fi; counter=\$((counter+1)); echo '%s'; done; """
    if toplevel:
        do += "echo -n 'All SPWs: '; pwd; "
    else:
        do += ' \"'
    write_bash_job_script(master, summaryScript, extn, do % (SPWs,dir,summaryScript,extn,"\$@ | grep -v 'PENDING\|COMPLETED'",header), 'view the progress \(for running or failed jobs\)', dir=dir,prefix=prefix)
    write_bash_job_script(master, fullSummaryScript, extn, do % (SPWs,dir,summaryScript,extn,'\$@',header), 'view the progress \(for all jobs\)', dir=dir,prefix=prefix)
    header = '------------------------------------------------------------------------------------------' + '-'*pad_length
    write_bash_job_script(master, errorScript, extn, do % (SPWs,dir,errorScript,extn,'',header), 'find errors \(after pipeline has run\)', dir=dir,prefix=prefix)
    write_bash_job_script(master, timingScript, extn, do % (SPWs,dir,timingScript,extn,'',header), 'display start and end timestamps \(after pipeline has run\)', dir=dir,prefix=prefix)

    #Close master submission script and make executable
    master.close()
    os.chmod(filename, 509)

    #[-R --run] pipeline in each SPW directory to create sbatch files that can be edited
    #TODO: fix this hackery!
    SPW_run_file='out.tmp'
    SPW_run_call = """for f in {%s,}; do if [ -d $f ]; then cd $f; %s --config ./%s --run --quiet; cd ..; else echo Directory $f doesn\\'t exist; fi; done""" % (','.join(SPWs.split(',')),os.path.split(THIS_PROG)[1],config)
    with open(SPW_run_file,'w') as out:
        out.write(SPW_run_call)
    os.system('bash {0}'.format(SPW_run_file))
    os.remove(SPW_run_file)

    #Submit script or output that it will not run
    if submit:
        logger.info('Running master script "{0}"'.format(filename))
        os.system('./{0}'.format(filename))
    else:
        logger.info('Master script "{0}" written in "{1}", but will not run.'.format(filename,os.path.split(os.getcwd())[1]))


def write_master(filename,config,scripts=[],submit=False,dir='jobScripts',pad_length=5,verbose=False, echo=True, dependencies='',slurm_kwargs={}):

    """Write master pipeline submission script, calling various sbatch files, and writing ancillary job scripts.

    Arguments:
    ----------
    filename : str
        Name of master pipeline submission script.
    config : str
        Path to config file.
    scripts : list, optional
        List of sbatch scripts to call in order.
    submit : bool, optional
        Submit jobs to SLURM queue immediately?
    dir : str, optional
        Name of directory to output ancillary job scripts.
    pad_length : int, optional
        Length to pad the SLURM sacct output columns.
    verbose : bool, optional
        Verbose output (inserted into master script)?
    echo : bool, optional
        Echo the pupose of each job script for the user?
    dependencies : str, optional
        Comma-separated list of SLURM job dependencies.
    slurm_kwargs : list, optional
        Parameters parsed from [slurm] section of config."""

    master = open(filename,'w')
    master.write('#!/bin/bash\n')
    timestamp = config_parser.get_key(config,'run','timestamp')
    if timestamp == '':
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        config_parser.overwrite_config(config, conf_dict={'timestamp' : "'{0}'".format(timestamp)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

    #Copy config file to TMP_CONFIG and inform user
    if verbose:
        master.write("\necho Copying \'{0}\' to \'{1}\', and using this to run pipeline.\n".format(config,TMP_CONFIG))
    master.write('cp {0} {1}\n'.format(config, TMP_CONFIG))

    #Expand a configured selfcal_part1/selfcal_part2 pair into the full loop chain
    scripts = expand_selfcal_loop_scripts(scripts, config, handle_run_sofia=True)
    scripts = expand_hi_combo_scripts(scripts, config)
    scripts = expand_cont_image_stage_scripts(scripts, config)

    command = 'sbatch'

    if dependencies != '':
        master.write('\n#Run after these dependencies\nDep={0}\n'.format(dependencies))
        command += " -d afterok:${Dep//,/:} --kill-on-invalid-dep=yes"
    master.write('\n#{0}\n'.format(scripts[0]))
    if verbose:
        master.write('echo Submitting {0} to SLURM queue with following command:\necho {1} {0}.\n'.format(scripts[0],command))
    master.write("IDs=$({0} {1} | cut -d ' ' -f4)\n".format(command,scripts[0]))
    scripts.pop(0)


    #Submit each script with dependency on all previous scripts, and extract job IDs
    for script in scripts:
        command = "sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes"
        master.write('\n#{0}\n'.format(script))
        if verbose:
            master.write('echo Submitting {0} to SLURM queue with following command\necho {1} {0}.\n'.format(script,command))
        master.write("IDs+=,$({0} {1} | cut -d ' ' -f4)\n".format(command,script))

    master.write('\n#Output message and create {0} directory\n'.format(dir))
    master.write('echo Submitted sbatch jobs with following IDs: $IDs\n') #DON'T CHANGE as this output is relied on by bash sed expression in write_spw_master()
    master.write('mkdir -p {0}\n'.format(dir))

    #Add time as extn to this pipeline run, to give unique filenames
    master.write('\n#Add time as extn to this pipeline run, to give unique filenames')
    master.write("\nDATE={0}".format(timestamp))
    extn = '_$DATE.sh'

    #Copy contents of config file to jobScripts directory
    master.write('\n#Copy contents of config file to {0} directory\n'.format(dir))
    master.write('cp {0} {1}/{2}_$DATE.txt\n'.format(config,dir,os.path.splitext(config)[0]))

    #Write each job script - kill script, summary script, error script, and timing script
    write_all_bash_jobs_scripts(master,extn,IDs='IDs',dir=dir,echo=echo,pad_length=pad_length,slurm_kwargs=slurm_kwargs,devel_partition=get_cluster_kwargs(config)['devel_partition'])

    #Close master submission script and make executable
    master.close()
    os.chmod(filename, 509)

    #Submit script or output that it will not run
    if submit:
        if echo:
            logger.info('Running master script "{0}"'.format(filename))
        os.system('./{0}'.format(filename))
    else:
        logger.info('Master script "{0}" written in "{1}", but will not run.'.format(filename,os.path.split(os.getcwd())[-1]))

def write_all_bash_jobs_scripts(master,extn,IDs,dir='jobScripts',echo=True,prefix='',pad_length=5, slurm_kwargs={}, devel_partition=DEFAULT_CLUSTER_KWARGS['devel_partition']):

    """Write all the bash job scripts for a given set of job IDs.

    Arguments:
    ----------
    master : class ``file``
        Master script to which to write contents.
    extn : str
        Extension to append to this job script (e.g. date & time).
    IDs : str
        Comma-separated list of job IDs
    dir : str, optional
        Directory to write this script into.
    echo : bool, optional
        Echo what this job script does for the user?
    prefix : str, optional
        Additional prefix to place on the beginning of these script names.
    pad_length : int, optional
        Length to pad the SLURM sacct output columns.
    slurm_kwargs : list, optional
        Parameters parsed from [slurm] section of config.
    devel_partition : str, optional
        SLURM partition to use for the generated cleanup script (see [cluster] section of config)."""

    #Add time as extn to this pipeline run, to give unique filenames
    killScript = prefix + 'killJobs'
    summaryScript = prefix + 'summary'
    errorScript = prefix + 'findErrors'
    timingScript = prefix + 'displayTimes'
    cleanupScript = prefix + 'cleanup'

    #Write each job script - kill script, summary script, and error script
    write_bash_job_script(master, killScript, extn, 'echo scancel ${0}'.format(IDs), 'kill all the jobs', dir=dir, echo=echo)
    do = """echo sacct -j ${0} --units=G -o "JobID%-15,JobName%-{1},Partition,Elapsed,NNodes%6,NTasks%6,NCPUS%5,MaxDiskRead,MaxDiskWrite,NodeList%20,TotalCPU,CPUTime,MaxRSS,State,ExitCode" \$@ """.format(IDs,15+pad_length)
    write_bash_job_script(master, summaryScript, extn, do, 'view the progress', dir=dir, echo=echo)
    do = """echo "for ID in {$%s,}; do files=\$(ls %s/*\$ID* 2>/dev/null | wc -l); if [ \$((files)) != 0 ]; then ls %s/*\$ID*; cat %s/*\$ID* | grep -i 'severe\|error' | grep -vi 'mpi\|The selected table has zero rows\|MeasTable::dUTC(Double)'; else echo %s/*\$ID* logs don\\'t exist \(yet\); fi; done" """ % (IDs,LOG_DIR,LOG_DIR,LOG_DIR,LOG_DIR)
    write_bash_job_script(master, errorScript, extn, do, 'find errors \(after pipeline has run\)', dir=dir, echo=echo)
    do = """echo "for ID in {$%s,}; do files=\$(ls %s/*\$ID* 2>/dev/null | wc -l); if [ \$((files)) != 0 ]; then logs=\$(ls %s/*\$ID* | sort -V); ls -f \$logs; cat \$(ls -tU \$logs) | grep INFO | head -n 1 | cut -d 'I' -f1; cat \$(ls -tr \$logs) | grep INFO | tail -n 1 | cut -d 'I' -f1; else echo %s/*\$ID* logs don\\'t exist \(yet\); fi; done" """ % (IDs,LOG_DIR,LOG_DIR,LOG_DIR)
    write_bash_job_script(master, timingScript, extn, do, 'display start and end timestamps \(after pipeline has run\)', dir=dir, echo=echo)

    # Create copy so original is unmodified
    cleanup_kwargs = deepcopy(slurm_kwargs)
    cleanup_kwargs['partition'] = devel_partition
    do = """echo "echo Removing the following: \$(ls -d *ms); %s rm -r *ms" """ % srun(cleanup_kwargs, qos=True, time=10, mem=0)
    write_bash_job_script(master, cleanupScript, extn, do, 'remove MSs/MMSs from this directory \(after pipeline has run\)', dir=dir, echo=echo)

def write_bash_job_script(master,filename,extn,do,purpose,dir='jobScripts',echo=True,prefix=''):

    """Write bash job script (e.g. jobs summary, kill all jobs, etc).

    Arguments:
    ----------
    master : class ``file``
        Master script to which to write contents.
    filename : str
        Filename of this job script.
    extn : str
        Extension to append to this job script (e.g. date & time).
    do : str
        Bash command to run in this job script.
    purpose : str
        Purpose of this script to append as comment.
    dir : str, optional
        Directory to write this script into.
    echo : bool, optional
        Echo what this job script does for the user?
    prefix : str, optional
        Additional prefix to place on the beginning of the script, called from the top level directory (instead of SPW directories)."""

    fname = '{0}/{1}{2}'.format(dir,filename,extn)
    do2 = ' ./{0}/{1}{2}{3} \$@ \"'.format(dir,prefix,filename,extn) if prefix != '' else ' '
    master.write('\n#Create {0}.sh file, make executable and symlink to current version\n'.format(filename))
    master.write('echo "#!/bin/bash" > {0}\n'.format(fname))
    master.write('{0}{1}>> {2}\n'.format(do,do2,fname))
    master.write('chmod +x {0}\n'.format(fname))
    master.write('ln -f -s {0} {1}.sh\n'.format(fname,filename))
    if echo:
        master.write('echo Run ./{0}.sh to {1}.\n'.format(filename,purpose))

def srun(arg_dict,qos=False,time=10,mem=4):

    """Return srun call, with certain parameters appended.

    Arguments:
    ----------
    arg_dict : dict
        Dictionary of arguments passed into this script, which is used to append parameters to srun call.
    qos : bool, optional
        Quality of service, set to True for interactive jobs, to increase likelihood of scheduling.
    mem : int, optional
        The memory in GB (per node) to use for this call.
    time : str, optional
        Time limit to use for this call, in the form d-hh:mm:ss.

    Returns:
    --------
    call : str
        srun call with arguments appended."""

    call = 'srun --time={0} --mem={1}GB --partition={2} --account={3}'.format(time,mem,arg_dict['partition'],arg_dict['account'])
    if qos:
        call += ' --qos qos-interactive'
    if arg_dict['exclude'] != '':
        call += ' --exclude={0}'.format(arg_dict['exclude'])
    if arg_dict['reservation'] != '':
        call += ' --reservation={0}'.format(arg_dict['reservation'])

    return call

def write_jobs(config, scripts=[], threadsafe=[], containers=[], num_precal_scripts=0, mpi_wrapper=MPI_WRAPPER, nodes=8, ntasks_per_node=4, mem=DEFAULT_MEM_GB,plane=1, partition='work',
               time='12:00:00', submit=False, name='', verbose=False, quiet=False, dependencies='', exclude='', account='pawsey1164', reservation='', modules=[], timestamp='', justrun=False):

    """Write a series of sbatch job files to calibrate a CASA MeasurementSet.

    Arguments:
    ----------
    config : str
        Path to config file.
    scripts : list (of paths), optional
        List of paths to scripts (assumed to be python -- i.e. extension .py) to call within seperate sbatch jobs.
    threadsafe : list (of bools), optional
        Are these scripts threadsafe (for MPI)? List assumed to be same length as scripts.
    containers : list (of paths), optional
        List of paths to singularity containers to use for each script. List assumed to be same length as scripts.
    num_precal_scripts : int, optional
        Number of precal scripts.
    mpi_wrapper : str, optional
        Path to MPI wrapper to use for threadsafe tasks (otherwise srun used).
    nodes : int, optional
        Number of nodes to use for this job.
    tasks : int, optional
        The number of tasks per node to use for this job.
    mem : int, optional
        The memory in GB (per node) to use for this job.
    plane : int, optional
        Distrubute tasks for this job using this block size before moving onto next node.
    partition : str, optional
        SLURM partition to use (default: "work").
    time : str, optional
        Time limit to use for all jobs, in the form d-hh:mm:ss.
    submit : bool, optional
        Submit jobs to SLURM queue immediately?
    name : str, optional
        Unique name to give this pipeline run, appended to the start of all job names.
    verbose : bool, optional
        Verbose output?
    quiet : bool, optional
        Activate quiet mode, with suppressed output?
    dependencies : str, optional
        Comma-separated list of SLURM job dependencies.
    exclude : str, optional
        SLURM worker nodes to exclude.
    account : str, optional
        SLURM accounting group for sbatch jobs.
    reservation : str, optional
        SLURM reservation to use.
    modules : list, optional
        Modules to load upon execution of sbatch script.
    timestamp : str, optional
        Timestamp to put on this run and related runs in SPW directories.
    justrun : bool, optionall
        Just run the pipeline without rebuilding each job script (if it exists)."""

    kwargs = locals()
    crosscal_kwargs = get_config_kwargs(config, 'crosscal', CROSSCAL_CONFIG_KEYS)
    cluster_kwargs = get_cluster_kwargs(config)
    pad_length = len(name)

    #Write sbatch file for each input python script
    for i,script in enumerate(scripts):
        jobname = os.path.splitext(os.path.split(script)[1])[0]

        #Use input SLURM configuration for threadsafe tasks, otherwise call srun with single node and single thread
        if threadsafe[i]:
            write_sbatch(script,'--config {0}'.format(TMP_CONFIG),nodes=nodes,tasks=ntasks_per_node,mem=mem,plane=plane,exclude=exclude,mpi_wrapper=mpi_wrapper,container=containers[i],partition=partition,
                        time=time,name=jobname,runname=name,SPWs=crosscal_kwargs['spw'],nspw=crosscal_kwargs['nspw'],account=account,reservation=reservation,modules=modules,justrun=justrun,cluster=cluster_kwargs)
        else:
            write_sbatch(script,'--config {0}'.format(TMP_CONFIG),nodes=1,tasks=1,mem=mem,plane=1,mpi_wrapper='srun',container=containers[i],partition=partition,time=time,name=jobname,
                        runname=name,SPWs=crosscal_kwargs['spw'],nspw=crosscal_kwargs['nspw'],exclude=exclude,account=account,reservation=reservation,modules=modules,justrun=justrun,cluster=cluster_kwargs)

    #Replace all .py with .sbatch
    scripts = [os.path.split(scripts[i])[1].replace('.py','.sbatch') for i in range(len(scripts))]
    precal_scripts = scripts[:num_precal_scripts]
    postcal_scripts = scripts[num_precal_scripts:]
    echo = False if quiet else True

    if crosscal_kwargs['nspw'] > 1:
        #Build master master script, calling each of the separate SPWs at once, precal scripts before this, and postcal scripts after this
        write_spw_master(MASTER_SCRIPT,config,SPWs=crosscal_kwargs['spw'],precal_scripts=precal_scripts,postcal_scripts=postcal_scripts,submit=submit,pad_length=pad_length,dependencies=dependencies,timestamp=timestamp,slurm_kwargs=kwargs)
    else:
        #Build master pipeline submission script
        write_master(MASTER_SCRIPT,config,scripts=scripts,submit=submit,pad_length=pad_length,verbose=verbose,echo=echo,dependencies=dependencies,slurm_kwargs=kwargs)


def default_config(arg_dict):

    """Generate default config file in current directory, pointing to MS, with fields and SLURM parameters set.

    Arguments:
    ----------
    arg_dict : dict
        Dictionary of arguments passed into this script, which is inserted into the config file under various sections."""

    filename = arg_dict['config']
    MS = arg_dict['MS']

    #Copy default config to current location
    copyfile('{0}/{1}'.format(SCRIPT_DIR,CONFIG),filename)

    #Add SLURM CL arguments to config file under section [slurm]
    slurm_dict = get_slurm_dict(arg_dict,SLURM_CONFIG_KEYS)
    for key in SLURM_CONFIG_STR_KEYS:
        if key in slurm_dict.keys(): slurm_dict[key] = "'{0}'".format(slurm_dict[key])

    #Overwrite CL parameters in config under section [slurm]
    config_parser.overwrite_config(filename, conf_dict=slurm_dict, conf_sec='slurm')

    #Add MS to config file under section [data] and dopol under section [run]
    config_parser.overwrite_config(filename, conf_dict={'vis' : "'{0}'".format(MS)}, conf_sec='data')
    config_parser.overwrite_config(filename, conf_dict={'dopol' : arg_dict['dopol']}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

    #uvsub.py/uvcontsub.py are needed by -H (HI cube imaging consumes their contsub'd output -- see
    #Phase 6) or by the standalone --contsub override (contsub'd visibilities without full cube
    #imaging); independent of both -2/-I, same as -H itself.
    want_contsub = arg_dict['hi_image'] or arg_dict['contsub']

    if not arg_dict['do2GC'] or not arg_dict['science_image'] or not arg_dict['hi_image'] or not want_contsub:
        #Roles to drop from postcal_scripts, keyed by declared pipeline_role rather than
        #literal filename -- same pattern as write_master()/write_spw_master()'s has_role()
        #(ed18bb7) and format_args()'s selfcal-present check (0e3c9ef).
        remove_roles = set()
        if not arg_dict['do2GC']:
            config_parser.remove_section(filename, 'selfcal')
            remove_roles |= {'selfcal_part1', 'selfcal_part2'}
        if not arg_dict['science_image']:
            #'[cont_image]' is now purely continuum-imaging-specific (uvcontsub.py's
            #fitspw/fitorder moved to their own '[contsub]' section below, and
            #hi_image.py has its own restfreq/imspw), so it's safe to drop it outright
            #whenever -I is off -- no more special-casing for -H/--contsub.
            config_parser.remove_section(filename, 'cont_image')
            remove_roles |= {'science_image', 'cont_sofia'}
        if not arg_dict['hi_image']:
            #-I and -H are independent (both can run together -- continuum self-cal
            #imaging + separate HI cube imaging in one pass), so this doesn't touch the
            #'cont_image'/'science_image' removal above.
            config_parser.remove_section(filename, 'hi_image')
            remove_roles |= {'hi_image', 'hi_sofia'}
        if not want_contsub:
            #Previously always ran whenever nspw > 1, regardless of -I/-H (a confirmed bug -- see
            #REFACTOR_PLAN.md's Phase 5 write-up): uvcontsub.py used to overwrite the shared
            #[data] vis key, so science_image.py (continuum) would silently end up imaging
            #contsub'd data if it happened to run afterward in postcal_scripts. Now gated
            #explicitly, and uvcontsub.py no longer touches [data] vis at all (writes
            #[run] hi_contsub_vis instead) -- see bookkeeping.get_hi_contsub_vis().
            config_parser.remove_section(filename, 'contsub')
            remove_roles |= {'uvsub', 'uvcontsub'}

        scripts = [s for s in arg_dict['postcal_scripts']
                   if script_registry.get_properties(s[0]).pipeline_role not in remove_roles]

        config_parser.overwrite_config(filename, conf_dict={'postcal_scripts' : scripts}, conf_sec='slurm')

    if not arg_dict['nofields']:
        #Don't call srun if option --local used
        if arg_dict['local']:
            mpi_wrapper = ''
        else:
            mpi_wrapper = srun(arg_dict)

        #Write and submit srun command to extract fields, and insert them into config file under section [fields]
        params =  '-B -M {MS} -C {config} -N {nodes} -t {ntasks_per_node}'.format(**arg_dict)
        if arg_dict['dopol']:
            params += ' -P'
        if arg_dict['verbose']:
            params += ' -v'
        command = write_command('read_ms.py', params, mpi_wrapper=mpi_wrapper, container=arg_dict['container'],logfile=False, mpi=False)
        logger.info('Extracting field IDs from MeasurementSet "{0}" using CASA.'.format(MS))
        logger.debug('Using the following command:\n\t{0}'.format(command))
        os.system(command)
    else:
        #Skip extraction of field IDs and assume we're not processing multiple SPWs
        logger.info('Skipping extraction of field IDs and assuming nspw=1.')
        config_parser.overwrite_config(filename, conf_dict={'nspw' : 1}, conf_sec='crosscal')

    #If dopol=True, replace second call of xx_yy_* scripts with xy_yx_* scripts
    #Check in config (not CL args), in case read_ms.py forces dopol=False, and assume we only want to set this for 'scripts'
    dopol = config_parser.get_key(filename, 'run', 'dopol')
    if dopol:
        count = 0
        for ind, ss in enumerate(arg_dict['scripts']):
            if ss[0] == 'xx_yy_solve.py' or ss[0] == 'xx_yy_apply.py':
                count += 1

            if count > 2:
                if ss[0] == 'xx_yy_solve.py':
                    arg_dict['scripts'][ind] = ('xy_yx_solve.py',arg_dict['scripts'][ind][1],arg_dict['scripts'][ind][2])
                if ss[0] == 'xx_yy_apply.py':
                    arg_dict['scripts'][ind] = ('xy_yx_apply.py',arg_dict['scripts'][ind][1],arg_dict['scripts'][ind][2])

        config_parser.overwrite_config(filename, conf_dict={'scripts' : arg_dict['scripts']}, conf_sec='slurm')

    logger.info('Config "{0}" generated.'.format(filename))

def get_slurm_dict(arg_dict,slurm_config_keys):

    """Build a slurm dictionary to be inserted into config file, using specified keys.

    Arguments:
    ----------
    arg_dict : dict
        Dictionary of arguments passed into this script, which is inserted into the config file under section [slurm].
    slurm_config_keys : list
        List of keys from arg_dict to insert into config file.

    Returns:
    --------
    slurm_dict : dict
        Dictionary to insert into config file under section [slurm]."""

    slurm_dict = {key:arg_dict[key] for key in slurm_config_keys}
    return slurm_dict

def pop_script(kwargs,script):

    """Pop script from list of scripts, list of threadsafe tasks, and list of containers.

    Arguments:
    ----------
    kwargs :  : dict
        Keyword arguments extracted from [slurm] section of config file, to be passed into write_jobs() function.
    script : str
        Name of script.

    Returns:
    --------
    popped : bool
        Was the script popped?"""

    popped = False
    if script in kwargs['scripts']:
        index = kwargs['scripts'].index(script)
        kwargs['scripts'].pop(index)
        kwargs['threadsafe'].pop(index)
        kwargs['containers'].pop(index)
        popped = True
    return popped

def format_args(config,submit,quiet,dependencies,justrun):

    """Format (and validate) arguments from config file, to be passed into write_jobs() function.

    Arguments:
    ----------
    config : str
        Path to config file.
    submit : bool
        Allow user to force submitting to queue immediately.
    quiet : bool
        Activate quiet mode, with suppressed output?
    dependencies : str
        Comma-separated list of SLURM job dependencies.
    justrun : bool
        Just run the pipeline without rebuilding each job script (if it exists).


    Returns:
    --------
    kwargs : dict
        Keyword arguments extracted from [slurm] section of config file, to be passed into write_jobs() function."""

    #Ensure all keys exist in these sections
    kwargs = get_config_kwargs(config,'slurm',SLURM_CONFIG_KEYS)
    data_kwargs = get_config_kwargs(config,'data',['vis'])
    field_kwargs = get_config_kwargs(config, 'fields', FIELDS_CONFIG_KEYS)
    crosscal_kwargs = get_config_kwargs(config, 'crosscal', CROSSCAL_CONFIG_KEYS)

    #Force submit=True if user has requested it during [-R --run]
    if submit:
        kwargs['submit'] = True

    #Ensure nspw is integer
    if type(crosscal_kwargs['nspw']) is not int:
        logger.warning("Argument 'nspw'={0} in '{1}' is not an integer. Will set to integer ({2}).".format(crosscal_kwargs['nspw']),config,int(crosscal_kwargs['nspw']))
        crosscal_kwargs['nspw'] = int(crosscal_kwargs['nspw'])

    spw = crosscal_kwargs['spw']
    nspw = crosscal_kwargs['nspw']
    mem = int(kwargs['mem'])

    if nspw > 1 and len(kwargs['scripts']) == 0:
        logger.warning('Setting nspw=1, since no "scripts" parameter in "{0}" is empty, so there\'s nothing run inside SPW directories.'.format(config))
        config_parser.overwrite_config(config, conf_dict={'nspw' : 1}, conf_sec='crosscal')
        nspw = 1

    #Check selfcal params
    if config_parser.has_section(config,'selfcal') and (any(script_registry.get_properties(i[0]).pipeline_role == 'selfcal_part1' for i in kwargs['postcal_scripts']) or any(script_registry.get_properties(i[0]).pipeline_role == 'selfcal_part1' for i in kwargs['scripts'])):
        selfcal_kwargs = get_config_kwargs(config, 'selfcal', SELFCAL_CONFIG_KEYS)
        params = bookkeeping.get_selfcal_params()
        if selfcal_kwargs['loop'] > 0:
            logger.warning("Starting with loop={0}, which is only valid if previous loops were successfully run in this directory.".format(selfcal_kwargs['loop']))
        #Find RACS outliers
        elif selfcal_kwargs['outlier_threshold'] != 0 and selfcal_kwargs['outlier_threshold'] != '':
            outlierfile = 'outliers.txt'
            outliers_loop0 = 'outliers_loop0.txt'
            CWD = os.path.split(os.getcwd())[1]
            if os.path.exists(outlierfile) and os.path.exists(outliers_loop0):
                logger.warning("Using existing outlier files '{0}' and '{1}' from '{2}'. Remove one of these files to derive outliers again.".format(outlierfile,outliers_loop0,CWD))
            elif os.path.exists('../{0}'.format(outlierfile)) and os.path.exists('../{0}'.format(outliers_loop0)):
                logger.warning("Assuming you're runnnig outlier imaging separately over several SPWs, so using one set of outliers by copying outlier file from '../{0}' and '../{1}' to '{2}'.".format(outlierfile,outliers_loop0,CWD))
                logger.warning("If these outlier files are irrelevant, please rename/remove one of them and run this step again.")
                copyfile('../{0}'.format(outlierfile), outlierfile)
                copyfile('../{0}'.format(outliers_loop0), outliers_loop0)
            else:
                if selfcal_kwargs['outlier_radius'] != '' and selfcal_kwargs['outlier_radius'] != 0.0:
                    txt = 'within {0} degrees'.format(selfcal_kwargs['outlier_radius'])
                else:
                    txt = 'within calculated search radius'
                logger.info('Populating sky model for selfcal using outlier_threshold={0}'.format(selfcal_kwargs['outlier_threshold']))
                logger.info('Querying Rapid ASAKP Continuum Survey (RACS) catalog around the target phase centre to identify outliers {0}. Please allow a moment for this.'.format(txt))
                sky_model_kwargs = deepcopy(kwargs)
                sky_model_kwargs['partition'] = get_cluster_kwargs(config)['devel_partition']
                mpi_wrapper = srun(sky_model_kwargs, qos=True, time=2, mem=0)
                command = write_command('set_sky_model.py', '-C {0}'.format(config), mpi_wrapper=mpi_wrapper, container=kwargs['container'],logfile=False, mpi=False)
                logger.debug('Running following command:\n\t{0}'.format(command))
                os.system(command)

    if config_parser.has_section(config,'cont_image'):
        imaging_kwargs = get_config_kwargs(config, 'cont_image', CONT_IMAGE_CONFIG_KEYS)

        valid_pbbands = ['LBand', 'SBand', 'UHF']
        if not any([pb.lower() in imaging_kwargs['pbband'].lower() for pb in valid_pbbands]):
            logger.warning('Invalid pbband found. Must be one of {}. If not fixed, will default to LBand.'.format(valid_pbbands))

    if config_parser.has_section(config,'hi_image'):
        hi_imaging_kwargs = get_config_kwargs(config, 'hi_image', HI_IMAGE_CONFIG_KEYS)

        valid_pbbands = ['LBand', 'SBand', 'UHF']
        if not any([pb.lower() in hi_imaging_kwargs['pbband'].lower() for pb in valid_pbbands]):
            logger.warning('Invalid pbband found in [hi_image]. Must be one of {}. If not fixed, will default to LBand.'.format(valid_pbbands))

    #If nspw = 1 and precal or postcal scripts present, overwrite config and reload
    if nspw == 1:
        if len(kwargs['precal_scripts']) > 0 or len(kwargs['postcal_scripts']) > 0:
            logger.warning('Appending "precal_scripts" to beginning of "scripts", and "postcal_scripts" to end of "scripts", since nspw=1. Overwritting this in "{0}".'.format(config))

            #Drop first instance of calc_refant.py from precal scripts in preference for one in scripts (after flag_round_1.py)
            if (any(script_registry.get_properties(i[0]).pipeline_role == 'calc_refant' for i in kwargs['precal_scripts']) and
                    any(script_registry.get_properties(i[0]).pipeline_role == 'calc_refant' for i in kwargs['scripts'])):
                kwargs['precal_scripts'].pop(next(idx for idx, i in enumerate(kwargs['precal_scripts'])
                                                   if script_registry.get_properties(i[0]).pipeline_role == 'calc_refant'))

            scripts = kwargs['precal_scripts'] + kwargs['scripts'] + kwargs['postcal_scripts']
            config_parser.overwrite_config(config, conf_dict={'scripts' : scripts}, conf_sec='slurm')
            config_parser.overwrite_config(config, conf_dict={'precal_scripts' : []}, conf_sec='slurm')
            config_parser.overwrite_config(config, conf_dict={'postcal_scripts' : []}, conf_sec='slurm')
            kwargs = get_config_kwargs(config,'slurm',SLURM_CONFIG_KEYS)
        else:
            scripts = kwargs['scripts']
    else:
        scripts = kwargs['precal_scripts'] + kwargs['postcal_scripts']

    kwargs['num_precal_scripts'] = len(kwargs['precal_scripts'])

    # Validate kwargs along with MS
    kwargs['MS'] = data_kwargs['vis']
    validate_args(kwargs,config)

    #Reformat scripts tuple/list, to extract scripts, threadsafe, and containers as parallel lists
    #Check that path to each script and container exists or is ''
    kwargs['scripts'] = [check_path(i[0]) for i in scripts]
    kwargs['threadsafe'] = [i[1] for i in scripts]
    kwargs['containers'] = [check_path(i[2]) for i in scripts]

    if not crosscal_kwargs['createmms']:
        logger.info("You've set 'createmms = False' in '{0}', so forcing 'keepmms = False'. Will use single CPU for every job other than 'partition.py', 'quick_tclean.py' and 'selfcal_*.py', if present.".format(config))
        config_parser.overwrite_config(config, conf_dict={'keepmms' : False}, conf_sec='crosscal')
        kwargs['threadsafe'] = [False]*len(scripts)

    elif not crosscal_kwargs['keepmms']:
        #Set threadsafe=False for split and postcal scripts (since working with MS not MMS).
        split_indices = [idx for idx, s in enumerate(kwargs['scripts']) if script_registry.get_properties(s).pipeline_role == 'split']
        if split_indices:
            kwargs['threadsafe'][split_indices[0]] = False
        if nspw != 1:
            kwargs['threadsafe'][kwargs['num_precal_scripts']:] = [False]*len(kwargs['postcal_scripts'])

    # #Set threadsafe=True for quick-tclean, selfcal_part1 or science_image as tclean uses MPI even for an MS (TODO: ensure it doesn't crash for flagging step)
    # for threadsafe_script in ['quick_tclean.py','selfcal_part1.py','science_image.py']:
    #     if threadsafe_script in kwargs['scripts']:
    #         kwargs['threadsafe'][kwargs['scripts'].index(threadsafe_script)] = True

    #Only reduce the memory footprint if we're not using all CPUs on each node
    if kwargs['ntasks_per_node'] < NTASKS_PER_NODE_LIMIT and nspw > 1:
        mem = int(mem // (nspw/2))

    dopol = config_parser.get_key(config, 'run', 'dopol')
    if not dopol and any(script_registry.get_properties(s).forces_dopol for s in kwargs['scripts']):
        logger.warning("Cross-hand calibration scripts 'xy_yx_*' found in scripts. Forcing dopol=True in '[run]' section of '{0}'.".format(config))
        config_parser.overwrite_config(config, conf_dict={'dopol' : True}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')

    includes_partition = any(script_registry.get_properties(script).is_spw_fanout for script in kwargs['scripts'])
    #If single correctly formatted spw, split into nspw directories, and process each spw independently
    if nspw > 1:
        #Write timestamp to this pipeline run
        kwargs['timestamp'] = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        config_parser.overwrite_config(config, conf_dict={'timestamp' : "'{0}'".format(kwargs['timestamp'])}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
        nspw = spw_split(spw, nspw, config, mem, crosscal_kwargs['badfreqranges'],kwargs['MS'],includes_partition, createmms = crosscal_kwargs['createmms'], fields=field_kwargs)
        config_parser.overwrite_config(config, conf_dict={'nspw' : "{0}".format(nspw)}, conf_sec='crosscal')

    #Pop script to calculate reference antenna if calcrefant=False. Assume it won't be in postcal scripts
    if not crosscal_kwargs['calcrefant']:
        if pop_script(kwargs,'calc_refant.py'):
            kwargs['num_precal_scripts'] -= 1

    #Replace empty containers with default container and remove unwanted kwargs
    for i in range(len(kwargs['containers'])):
        if kwargs['containers'][i] == '':
            kwargs['containers'][i] = kwargs['container']
    kwargs.pop('container')
    kwargs.pop('MS')
    kwargs.pop('precal_scripts')
    kwargs.pop('postcal_scripts')
    kwargs['quiet'] = quiet
    kwargs['justrun'] = justrun

    #Force overwrite of dependencies
    if dependencies != '':
        kwargs['dependencies'] = dependencies

    if len(kwargs['scripts']) == 0 and nspw == 1:
        logger.error('Nothing to do. Please insert scripts into "scripts" parameter in "{0}".'.format(config))
        #sys.exit(1)

    #If everything up until here has passed, we can copy config file to TMP_CONFIG (in case user runs sbatch manually) and inform user
    logger.debug("Copying '{0}' to '{1}', and using this to run pipeline.".format(config,TMP_CONFIG))
    copyfile(config, TMP_CONFIG)
    if not quiet:
        logger.warning("Changing [slurm] section in your config will have no effect unless you [-R --run] again.")

    return kwargs

def linspace(lower,upper,length):

    """Basically np.linspace, but without needing to import numpy..."""

    return [lower + x*(upper-lower)/float(length-1) for x in range(length)]

def get_spw_bounds(spw):

    """Get upper and lower bounds of spw.

    Arguments:
    ----------
    spw : str
        CASA spectral window in MHz.

    Returns:
    --------
    low : float
        Lower bound of spw.
    high : float
        Higher bound of spw.
    unit : str
        Unit of spw.
    func : function
        Function to apply to spectral window (i.e. int for SPW channel range, otherwise float)."""

    bounds = spw.split(':')[-1].split('~')
    if ',' not in spw and ':' in spw and '~' in spw and len(bounds) == 2 and bounds[1] != '':
        high,unit=re.search(r'(\d+\.*\d*)(\w*)',bounds[1]).groups()
        func = int if unit == '' or '.' not in bounds[0] else float
        low = func(bounds[0])
        func = int if unit == '' or '.' not in high else float
        high = func(high)

        if unit != 'MHz':
            logger.warning('Please use SPW unit "MHz", to ensure the best performance (e.g. not processing entirely flagged frequency ranges).')

    else:
        return None

    return low,high,unit,func

def spw_split(spw,nspw,config,mem,badfreqranges,MS,partition,createmms=True,remove=True,fields={}):

    """Split into N SPWs, placing an instance of the pipeline into N directories, each with 1 Nth of the bandwidth.

    Arguments:
    ----------
    spw : str
        spw parameter from config.
    nspw : int
        Number of spectral windows to split into.
    config : str
        Path to config file.
    mem : int
        Memory in GB to use per instance.
    badfreqranges : list
        List of bad frequency ranges in MHz.
    MS : str
        Path to CASA MeasurementSet.
    partition : bool
        Does this run include the partition step?
    createmms : bool
        Create MMS as output?
    remove : bool, optional
        Remove SPWs completely encompassed by bad frequency ranges?
    fields : dict, optional
        Field names, so we can do some visname renaming hackery!

    Returns:
    --------
    nspw : int
        New nspw, potentially a lower value than input (if any SPWs completely encompassed by badfreqranges)."""

    if get_spw_bounds(spw) != None:
        #Write nspw frequency ranges
        low,high,unit,func = get_spw_bounds(spw)
        interval=func((high-low)/float(nspw))
        lo=linspace(low,high-interval,nspw)
        hi=linspace(low+interval,high,nspw)
        SPWs=[]

        #Remove SPWs entirely encompassed by bad frequency ranges (only for MHz unit)
        for i in range(len(lo)):
            SPWs.append('{0}{1}~{2}{3}'.format(SPW_PREFIX, func(lo[i]),func(hi[i]),unit))

    elif ',' in spw:
        SPWs = spw.split(',')
        unit = get_spw_bounds(SPWs[0])[2]
        if len(SPWs) != nspw:
            logger.error("nspw ({0}) not equal to number of separate SPWs ({1} in '{2}') from '{3}'. Setting to nspw={1}.".format(nspw,len(SPWs),spw,config))
            nspw = len(SPWs)
    else:
        logger.error("Can't split into {0} SPWs using SPW format '{1}'. Using nspw=1 in '{2}'.".format(nspw,spw,config))
        return 1

    #Remove any SPWs completely encompassed by bad frequency ranges
    i=0
    while i < nspw:
        badfreq = False
        low,high = get_spw_bounds(SPWs[i])[0:2]
        if unit == 'MHz' and remove:
            for freq in badfreqranges:
                bad_low,bad_high = get_spw_bounds('{0}{1}'.format(SPW_PREFIX,freq))[0:2]
                if low >= bad_low and high <= bad_high:
                    logger.info("Won't process spw '{0}{1}~{2}{3}', since it's completely encompassed by bad frequency range '{3}'.".format(SPW_PREFIX,low,high,unit,freq))
                    badfreq = True
                    break
        if badfreq:
            SPWs.pop(i)
            i -= 1
            nspw -= 1
        i += 1

    #Overwrite config with new SPWs
    config_parser.overwrite_config(config, conf_dict={'spw' : "'{0}'".format(','.join(SPWs))}, conf_sec='crosscal')

    #Create each spw as directory and place config in there
    logger.info("Making {0} directories for SPWs ({1}) and copying '{2}' to each of them.".format(nspw,SPWs,config))
    for spw in SPWs:
        spw_config = '{0}/{1}'.format(spw.replace(SPW_PREFIX,''),config)
        if not os.path.exists(spw.replace(SPW_PREFIX,'')):
            os.mkdir(spw.replace(SPW_PREFIX,''))
        copyfile(config, spw_config)
        config_parser.overwrite_config(spw_config, conf_dict={'spw' : "'{0}'".format(spw)}, conf_sec='crosscal')
        config_parser.overwrite_config(spw_config, conf_dict={'nspw' : 1}, conf_sec='crosscal')
        config_parser.overwrite_config(spw_config, conf_dict={'mem' : mem}, conf_sec='slurm')
        config_parser.overwrite_config(spw_config, conf_dict={'calcrefant' : False}, conf_sec='crosscal')
        config_parser.overwrite_config(spw_config, conf_dict={'precal_scripts' : []}, conf_sec='slurm')
        config_parser.overwrite_config(spw_config, conf_dict={'postcal_scripts' : []}, conf_sec='slurm')
        #Look 1 directory up when using relative path
        if MS[0] != '/':
            config_parser.overwrite_config(spw_config, conf_dict={'vis' : "'../{0}'".format(MS)}, conf_sec='data')
        if not partition:
            basename, ext = os.path.splitext(MS.rstrip('/ '))
            filebase = os.path.split(basename)[1]
            extn = 'mms' if createmms else 'ms'

            #Hack to rename vis if setting as specific field (e.g. as target field when running selfcal)
            prefix,suffix = os.path.splitext(filebase)
            if suffix[1:] != '' and suffix[1:] in fields.values():
                extn = '{0}.{1}'.format(suffix[1:],extn)
                filebase = prefix

            vis = '{0}.{1}.{2}'.format(filebase,spw.replace(SPW_PREFIX,''),extn)
            logger.warning("Since script with 'partition' in its name isn't present in '{0}', assuming partition has already been done, and setting vis='{1}' in '{2}'. If '{1}' doesn't exist, please update '{2}', as the pipeline will not launch successfully.".format(config,vis,spw_config))
            orig_vis = config_parser.get_key(spw_config, 'data', 'vis')
            config_parser.overwrite_config(spw_config, conf_dict={'orig_vis' : "'{0}'".format(orig_vis)}, conf_sec='run', sec_comment='# Internal variables for pipeline execution')
            config_parser.overwrite_config(spw_config, conf_dict={'vis' : "'{0}'".format(vis)}, conf_sec='data')

    return nspw

def get_config_kwargs(config,section,expected_keys):

    """Return kwargs from config section. Check section exists, and that all expected keys are present, otherwise raise KeyError.

    Arguments:
    ----------
    config : str
        Path to config file.
    section : str
        Config section from which to extract kwargs.
    expected_keys : list
        List of expected keys.

    Returns:
    --------
    kwargs : dict
        Keyword arguments from this config section."""

    config_dict = config_parser.parse_config(config)[0]

    #Ensure section exists, otherwise raise KeyError
    if section not in config_dict.keys():
        raise KeyError("Config file '{0}' has no section [{1}]. Please insert section or build new config with [-B --build].".format(config,section))

    kwargs = config_dict[section]

    #Check for any unknown keys and display warning
    unknown_keys = list(set(kwargs) - set(expected_keys))
    if len(unknown_keys) > 0:
        logger.warning("Unknown keys {0} present in section [{1}] in '{2}'.".format(unknown_keys,section,config))

    #Check that expected keys are present, otherwise raise KeyError
    missing_keys = list(set(expected_keys) - set(kwargs))
    if len(missing_keys) > 0:
        raise KeyError("Keys {0} missing from section [{1}] in '{2}'. Please add these keywords to '{2}', or else run [-B --build] step again.".format(missing_keys,section,config))

    return kwargs

def get_cluster_kwargs(config):

    """Return the '[cluster]' section's kwargs from a config file, falling back to
    DEFAULT_CLUSTER_KWARGS (unchanged) for a config predating this section, rather than hard-
    requiring it via get_config_kwargs() -- so existing configs built before Phase 3 of the
    Pawsey refactor keep working without regenerating them.

    Arguments:
    ----------
    config : str
        Path to config file.

    Returns:
    --------
    kwargs : dict
        Keyword arguments from the '[cluster]' section, or DEFAULT_CLUSTER_KWARGS."""

    if config_parser.has_section(config,'cluster'):
        return get_config_kwargs(config,'cluster',CLUSTER_CONFIG_KEYS)
    return DEFAULT_CLUSTER_KWARGS

def setup_logger(config,verbose=False):

    """Setup logger at debug or info level according to whether verbose option selected (via command line or config file).

    Arguments:
    ----------
    config : str
        Path to config file.
    verbose : bool
        Verbose output? This will display all logger debug output."""

    #Overwrite with verbose mode if set to True in config file
    if not verbose:
        config_dict = config_parser.parse_config(config)[0]
        if 'slurm' in config_dict.keys() and 'verbose' in config_dict['slurm']:
            verbose = config_dict['slurm']['verbose']

    loglevel = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(loglevel)

def main():

    #Parse command-line arguments, and setup logger
    args = parse_args()
    setup_logger(args.config,args.verbose)

    #Mutually exclusive arguments - display version, build config file or run pipeline
    if args.version:
        logger.info('This is version {0}'.format(__version__))
    if args.license:
        logger.info(license)
    if args.build:
        default_config(vars(args))
    if args.run:
        kwargs = format_args(args.config,args.submit,args.quiet,args.dependencies,args.justrun)
        write_jobs(args.config, **kwargs)

if __name__ == "__main__":
    main()
