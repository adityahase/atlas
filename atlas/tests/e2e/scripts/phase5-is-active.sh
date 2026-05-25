#!/bin/bash
# Phase 5 e2e: assert the VM's systemd unit is active.
set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?}"
sudo systemctl is-active "firecracker-vm@${VIRTUAL_MACHINE_NAME}.service"
