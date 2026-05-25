#!/bin/bash
# Phase 3 e2e: verify the bootstrap-uploaded files are in place.
set -euo pipefail

for path in \
    /var/lib/atlas/bin/vm-network-up.sh \
    /var/lib/atlas/bin/vm-network-down.sh \
    /etc/systemd/system/firecracker-vm@.service; do
    test -f "$path"
    echo "$(basename "$path") OK"
done
