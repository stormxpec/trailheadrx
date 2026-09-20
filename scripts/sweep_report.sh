#!/bin/sh
# Print the last sweep's log, or say there is none.
if [ -f /data/sweep.log ]; then cat /data/sweep.log; else echo "no sweep log found"; fi
