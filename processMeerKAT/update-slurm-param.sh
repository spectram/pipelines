#!/bin/bash

if [ "$1" = "-h" ]; then
        echo "Input: \$1 - Param; \$2 - Value; \$3 - Files"
else
        sed -i "s/$1.*/$1=$2/g" "${@:3}"
fi
