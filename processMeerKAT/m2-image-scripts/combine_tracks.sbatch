#!/bin/bash
#SBATCH --account=b156-meerrings-ag
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=120GB
#SBATCH --job-name=combine_tracks
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
#SBATCH --partition=Main
#SBATCH --time=10:00:00

srun singularity exec /idia/software/containers/casa-6.6.0-modular.sif python combine_tracks.py
