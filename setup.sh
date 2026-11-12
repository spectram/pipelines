dir=$(dirname $BASH_SOURCE)/processMeerKAT
export PATH=$PATH:$dir
export PYTHONPATH=$PYTHONPATH:$dir
export SINGULARITYENV_PYTHONPATH="$PYTHONPATH:\$PYTHONPATH"
_d=$(cd "$(dirname $BASH_SOURCE)" && pwd)
git config --global --get-all safe.directory | grep -qxF "$_d" || git config --global --add safe.directory "$_d"
unset _d
