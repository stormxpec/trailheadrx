#!/bin/sh
# Print one word the workflow can grep for: DONE or RUNNING.
if [ -f /data/sweep.done ]; then echo "STATUS=DONE"; else echo "STATUS=RUNNING"; fi
