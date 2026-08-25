#!/bin/bash
cp fixture_config.txt .config.tmp

#partition.sbatch
IDs=$(sbatch partition.sbatch | cut -d ' ' -f4)

#validate_input.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes validate_input.sbatch | cut -d ' ' -f4)

#flag_round_1.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes flag_round_1.sbatch | cut -d ' ' -f4)

#setjy.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes setjy.sbatch | cut -d ' ' -f4)

#xx_yy_solve.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes xx_yy_solve.sbatch | cut -d ' ' -f4)

#xx_yy_apply.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes xx_yy_apply.sbatch | cut -d ' ' -f4)

#flag_round_2.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes flag_round_2.sbatch | cut -d ' ' -f4)

#xx_yy_solve.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes xx_yy_solve.sbatch | cut -d ' ' -f4)

#xx_yy_apply.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes xx_yy_apply.sbatch | cut -d ' ' -f4)

#split.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes split.sbatch | cut -d ' ' -f4)

#quick_tclean.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes quick_tclean.sbatch | cut -d ' ' -f4)

#plotcal_spw.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes plotcal_spw.sbatch | cut -d ' ' -f4)

#selfcal_part1.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part1.sbatch | cut -d ' ' -f4)

#selfcal_part2.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part2.sbatch | cut -d ' ' -f4)

#selfcal_part1.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part1.sbatch | cut -d ' ' -f4)

#selfcal_part2.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part2.sbatch | cut -d ' ' -f4)

#selfcal_part1.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part1.sbatch | cut -d ' ' -f4)

#selfcal_part2.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part2.sbatch | cut -d ' ' -f4)

#run_sofia.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes run_sofia.sbatch | cut -d ' ' -f4)

#selfcal_part1.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part1.sbatch | cut -d ' ' -f4)

#selfcal_part2.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes selfcal_part2.sbatch | cut -d ' ' -f4)

#uvsub.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes uvsub.sbatch | cut -d ' ' -f4)

#uvcontsub.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes uvcontsub.sbatch | cut -d ' ' -f4)

#hi_image.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes hi_image.sbatch | cut -d ' ' -f4)

#hi_sofia.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes hi_sofia.sbatch | cut -d ' ' -f4)

#hi_image.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes hi_image.sbatch | cut -d ' ' -f4)

#hi_sofia.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes hi_sofia.sbatch | cut -d ' ' -f4)

#science_image.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes science_image.sbatch | cut -d ' ' -f4)

#cont_sofia.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes cont_sofia.sbatch | cut -d ' ' -f4)

#science_image.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes science_image.sbatch | cut -d ' ' -f4)

#cont_sofia.sbatch
IDs+=,$(sbatch -d afterok:${IDs//,/:} --kill-on-invalid-dep=yes cont_sofia.sbatch | cut -d ' ' -f4)

#Output message and create jobScripts directory
echo Submitted sbatch jobs with following IDs: $IDs
mkdir -p jobScripts

#Add time as extn to this pipeline run, to give unique filenames
DATE=FIXTURE
#Copy contents of config file to jobScripts directory
cp fixture_config.txt jobScripts/fixture_config_$DATE.txt

#Create killJobs.sh file, make executable and symlink to current version
echo "#!/bin/bash" > jobScripts/killJobs_$DATE.sh
echo scancel $IDs >> jobScripts/killJobs_$DATE.sh
chmod +x jobScripts/killJobs_$DATE.sh
ln -f -s jobScripts/killJobs_$DATE.sh killJobs.sh
echo Run ./killJobs.sh to kill all the jobs.

#Create summary.sh file, make executable and symlink to current version
echo "#!/bin/bash" > jobScripts/summary_$DATE.sh
echo sacct -j $IDs --units=G -o "JobID%-15,JobName%-15,Partition,Elapsed,NNodes%6,NTasks%6,NCPUS%5,MaxDiskRead,MaxDiskWrite,NodeList%20,TotalCPU,CPUTime,MaxRSS,State,ExitCode" \$@  >> jobScripts/summary_$DATE.sh
chmod +x jobScripts/summary_$DATE.sh
ln -f -s jobScripts/summary_$DATE.sh summary.sh
echo Run ./summary.sh to view the progress.

#Create findErrors.sh file, make executable and symlink to current version
echo "#!/bin/bash" > jobScripts/findErrors_$DATE.sh
echo "for ID in {$IDs,}; do files=\$(ls logs/*\$ID* 2>/dev/null | wc -l); if [ \$((files)) != 0 ]; then ls logs/*\$ID*; cat logs/*\$ID* | grep -i 'severe\|error' | grep -vi 'mpi\|The selected table has zero rows\|MeasTable::dUTC(Double)'; else echo logs/*\$ID* logs don\'t exist \(yet\); fi; done"  >> jobScripts/findErrors_$DATE.sh
chmod +x jobScripts/findErrors_$DATE.sh
ln -f -s jobScripts/findErrors_$DATE.sh findErrors.sh
echo Run ./findErrors.sh to find errors \(after pipeline has run\).

#Create displayTimes.sh file, make executable and symlink to current version
echo "#!/bin/bash" > jobScripts/displayTimes_$DATE.sh
echo "for ID in {$IDs,}; do files=\$(ls logs/*\$ID* 2>/dev/null | wc -l); if [ \$((files)) != 0 ]; then logs=\$(ls logs/*\$ID* | sort -V); ls -f \$logs; cat \$(ls -tU \$logs) | grep INFO | head -n 1 | cut -d 'I' -f1; cat \$(ls -tr \$logs) | grep INFO | tail -n 1 | cut -d 'I' -f1; else echo logs/*\$ID* logs don\'t exist \(yet\); fi; done"  >> jobScripts/displayTimes_$DATE.sh
chmod +x jobScripts/displayTimes_$DATE.sh
ln -f -s jobScripts/displayTimes_$DATE.sh displayTimes.sh
echo Run ./displayTimes.sh to display start and end timestamps \(after pipeline has run\).

#Create cleanup.sh file, make executable and symlink to current version
echo "#!/bin/bash" > jobScripts/cleanup_$DATE.sh
echo "echo Removing the following: \$(ls -d *ms); srun --nodes=1 --ntasks=1 --time=10 --mem=0GB --partition=Devel --account=pawsey1164 --qos qos-interactive rm -r *ms"  >> jobScripts/cleanup_$DATE.sh
chmod +x jobScripts/cleanup_$DATE.sh
ln -f -s jobScripts/cleanup_$DATE.sh cleanup.sh
echo Run ./cleanup.sh to remove MSs/MMSs from this directory \(after pipeline has run\).
