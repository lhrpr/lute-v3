#!/bin/sh
#
# Check that the folders given in the baked-in config
# (lute/config/config.yml.docker) are in fact mounted.
#
# Hosts that only allow a single volume (e.g. Railway, which gives one
# volume per service) keep backups under the data volume instead, and
# set LUTE_REQUIRED_MOUNTS to just that one path.

REQUIRED_MOUNTS="${LUTE_REQUIRED_MOUNTS:-/lute_data /lute_backup}"

for d in $REQUIRED_MOUNTS; do
    checkdir=`mount | grep "$d"`
    if [ -z "$checkdir" ]
    then
        echo ""
        echo "-------------------------------------------------------"
        echo "$d container directory is not mounted, quitting."
        echo ""
        echo "Lute (containerized) writes to: $REQUIRED_MOUNTS"
        echo "ALL of these must be mounted."
        echo ""
        echo "If these folders are not mounted from host directories,"
        echo "then the writes will go to the container's writable layer,"
        echo "and would be destroyed if the container were deleted."
        echo "That would mean loss of data, so this check prevents it."
        echo ""
        echo "Please ensure you mount a host directory,"
        echo "either in your docker-compose.yml or in your docker run."
        echo "-------------------------------------------------------"
        echo ""
        # Non-zero so the host reports a failed deploy rather than a
        # container that exited cleanly.
        exit 1
    fi
done

# Railway (and most PaaS hosts) inject the port to listen on.
python -m lute.main --port "${PORT:-5001}"
