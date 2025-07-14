#!/bin/bash

#SBATCH --job-name='HIcube'
#SBATCH --output=%x-%j-stdout.log
#SBATCH --error=%x-%j-stderr.log
#SBATCH --ntasks=11
#SBATCH --mail-user=
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80
#SBATCH --cpus-per-task=1
#SBATCH --mem=200GB
#SBATCH --nodes=1
#SBATCH --time=48:00:00

echo "Submitting Parallel Slurm job with $SLURM_NTASKS CPU's and $SLURM_MEM_PER_NODE memory"
/idia/software/pipelines/casa-prerelease-5.3.0-115.el7/bin/mpicasa -n $SLURM_NTASKS singularity exec /idia/software/containers/casa-stable-5.7.0.simg xvfb-run -a casa --nologger --nogui --logfile ${SLURM_JOB_NAME}-${SLURM_JOB_ID}-casa.log -c hicube1.py
