#!/bin/bash

#SBATCH --job-name='sofia2'
#SBATCH --output=%x-%j-stdout.log
#SBATCH --error=%x-%j-stderr.log
#SBATCH --ntasks=1
#SBATCH --mail-user=sriram.sankar@icrar.org
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80
#SBATCH --cpus-per-task=8
#SBATCH --mem=64GB
#SBATCH --nodes=1
#SBATCH --time=01:00:00

echo "Submitting Multi-thread Slurm job with $SLURM_MEM_PER_NODE memory"
singularity exec /idia/software/containers/SoFiA-V2.6.7-2025-03-12.sif sofia rebinsof.file

