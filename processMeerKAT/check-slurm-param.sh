#!/bin/bash

if [ "$1" = "-h" ]; then
        echo "Input: \$1 - Param; \$2 - Value (bogus); \$3 - Files"
else
        cat "${@:3}" | grep $1 #=$2
fi
