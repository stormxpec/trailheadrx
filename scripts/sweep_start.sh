#!/bin/sh
# Start the nightly sweep in the background on the Fly machine. Called by the
# GitHub workflow through `fly machine exec`, which cannot carry shell quoting,
# so the logic lives here. Writes /data/sweep.log and, when finished,
# /data/sweep.done. Extra arguments are passed to the sweep (e.g. --pairs 12).
set -u
rm -f /data/sweep.done
nohup sh -c "python -m trailheadrx sweep $* ; touch /data/sweep.done" > /data/sweep.log 2>&1 &
echo "sweep started in background with: $*"
