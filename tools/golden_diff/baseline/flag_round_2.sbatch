#!/bin/bash
#SBATCH --account=pawsey1164
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=9
#SBATCH --cpus-per-task=14
#SBATCH --mem=226GB
#SBATCH --job-name=flag_round_2
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=work
#SBATCH --time=12:00:00

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
module load singularity/4.1.0-mpi


export SINGULARITYENV_LD_LIBRARY_PATH="/opt/casacore/lib:$SINGULARITYENV_LD_LIBRARY_PATH"
srun -c 14 singularity exec --env LD_PRELOAD=/opt/spack/opt/spack/linux-zen2/openssl-3.4.1-kd6nwzlpilohkirzkrhioadlkvonnjkz/lib64/libcrypto.so.3:/opt/spack/opt/spack/linux-zen2/openssl-3.4.1-kd6nwzlpilohkirzkrhioadlkvonnjkz/lib64/libssl.so.3 --env PYTHONPATH=/software/projects/pawsey1164/ssankar/containers/idianext_mpi4py:__REPO_ROOT__/processMeerKAT --env OMPI_COMM_WORLD_RANK=$PMI_RANK --bind /var/spool/slurmd /software/projects/pawsey1164/ssankar/containers/idianext.sif  /opt/venv/bin/python3 __REPO_ROOT__/processMeerKAT/crosscal_scripts/flag_round_2.py --config .config.tmp