#!/bin/bash
# Probe: exits 0 if the named image's rootfs exists on the server.
# Used by Virtual Machine.provision() to fail fast when an operator forgot
# to sync the image to the server.
#
# Inputs:
#   IMAGE_NAME       - directory under /var/lib/atlas/images
#   ROOTFS_FILENAME  - filename to check for

set -euo pipefail

: "${IMAGE_NAME:?required}"
: "${ROOTFS_FILENAME:?required}"

path="/var/lib/atlas/images/${IMAGE_NAME}/${ROOTFS_FILENAME}"
if [ ! -f "$path" ]; then
    echo "Image '${IMAGE_NAME}' is not present on server (missing ${path}). Sync the image first." >&2
    exit 1
fi
echo "image present"
