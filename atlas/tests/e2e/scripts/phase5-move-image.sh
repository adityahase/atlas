#!/bin/bash
# Phase 5 e2e: move an image rootfs aside/back to test the absent-image path.
set -euo pipefail

: "${IMAGE_NAME:?}"
: "${ROOTFS_FILENAME:?}"
: "${DIRECTION:?}"  # aside | back

path="/var/lib/atlas/images/${IMAGE_NAME}/${ROOTFS_FILENAME}"
case "$DIRECTION" in
    aside) sudo mv "$path" "${path}.bak" ;;
    back)  sudo mv "${path}.bak" "$path" ;;
    *) echo "DIRECTION must be aside or back" >&2; exit 1 ;;
esac
