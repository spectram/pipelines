#!/bin/bash
#SBATCH --account=pawsey1164
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=18
#SBATCH --mem=32GB
#SBATCH --job-name=run_sofia
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=work
#SBATCH --time=12:00:00

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
module load singularity/4.1.0-nohost


srun -c 18 singularity exec /software/projects/pawsey1164/ssankar/containers/SoFiA-V2.6.7-2025-03-12.sif  python3 __REPO_ROOT__/processMeerKAT/aux_scripts/run_sofia.py --config .config.tmp