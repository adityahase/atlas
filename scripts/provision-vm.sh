#!/bin/bash
# Provision one Firecracker VM on this server. Single task: prepares disk,
# config, networking, then starts the systemd unit. Run once per VM.
#
# Inputs (environment variables):
#   VIRTUAL_MACHINE_NAME  - UUID, used for directory, tap, systemd instance
#   IMAGE_NAME            - directory under /var/lib/atlas/images
#   KERNEL_FILENAME       - filename inside the image directory
#   ROOTFS_FILENAME       - filename inside the image directory
#   VCPUS                 - integer
#   MEMORY_MB             - integer
#   DISK_GB               - integer, final rootfs size for this VM
#   MAC_ADDRESS           - e.g. 06:00:01:02:03:04
#   TAP_DEVICE            - e.g. atlas-<first 10 chars of vm name>
#   VIRTUAL_MACHINE_IPV6  - the VM's address inside the server's /124
#   SSH_PUBLIC_KEY        - injected into the rootfs

set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?required}"
: "${IMAGE_NAME:?required}"
: "${KERNEL_FILENAME:?required}"
: "${ROOTFS_FILENAME:?required}"
: "${VCPUS:?required}"
: "${MEMORY_MB:?required}"
: "${DISK_GB:?required}"
: "${MAC_ADDRESS:?required}"
: "${TAP_DEVICE:?required}"
: "${VIRTUAL_MACHINE_IPV6:?required}"
: "${SSH_PUBLIC_KEY:?required}"

image_directory="/var/lib/atlas/images/${IMAGE_NAME}"
vm_directory="/var/lib/atlas/virtual-machines/${VIRTUAL_MACHINE_NAME}"

sudo install -d -m 0700 "$vm_directory"
sudo install -d -m 0700 "${vm_directory}/log"

# 1. Per-VM rootfs: copy and resize.
rootfs_path="${vm_directory}/rootfs.ext4"
if [ ! -f "$rootfs_path" ]; then
    sudo cp "${image_directory}/${ROOTFS_FILENAME}" "${rootfs_path}.part"
    sudo truncate -s "${DISK_GB}G" "${rootfs_path}.part"
    sudo e2fsck -fy "${rootfs_path}.part" >/dev/null 2>&1 || true
    sudo resize2fs "${rootfs_path}.part" >/dev/null
    sudo mv "${rootfs_path}.part" "$rootfs_path"
fi

# 2. Inject SSH key and per-VM network env.
mount_point="$(sudo mktemp -d /tmp/atlas-mount-XXXXXX)"
sudo mount -o loop "$rootfs_path" "$mount_point"
trap 'sudo umount "$mount_point" 2>/dev/null || true; sudo rmdir "$mount_point" 2>/dev/null || true' EXIT

sudo install -d -m 0700 "${mount_point}/root/.ssh"
printf '%s\n' "$SSH_PUBLIC_KEY" | sudo install -m 0600 /dev/stdin "${mount_point}/root/.ssh/authorized_keys"

sudo install -m 0644 /dev/stdin "${mount_point}/etc/atlas-network.env" <<EOF
VIRTUAL_MACHINE_IPV6=${VIRTUAL_MACHINE_IPV6}
EOF

sudo umount "$mount_point"
sudo rmdir "$mount_point"
trap - EXIT

# 3. Firecracker config.
sudo install -m 0644 /dev/stdin "${vm_directory}/firecracker.json" <<EOF
{
  "boot-source": {
    "kernel_image_path": "${image_directory}/${KERNEL_FILENAME}",
    "boot_args": "console=ttyS0 reboot=k panic=1"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "${rootfs_path}",
      "is_root_device": true,
      "is_read_only": false
    }
  ],
  "network-interfaces": [
    {
      "iface_id": "eth0",
      "guest_mac": "${MAC_ADDRESS}",
      "host_dev_name": "${TAP_DEVICE}"
    }
  ],
  "machine-config": {
    "vcpu_count": ${VCPUS},
    "mem_size_mib": ${MEMORY_MB}
  }
}
EOF

# 4. Sidecar that vm-network-up.sh reads. Stable across host reboots.
sudo install -m 0644 /dev/stdin "${vm_directory}/network.env" <<EOF
TAP_DEVICE=${TAP_DEVICE}
VIRTUAL_MACHINE_IPV6=${VIRTUAL_MACHINE_IPV6}
EOF

# 5. Enable and start the systemd unit.
sudo systemctl enable --now "firecracker-vm@${VIRTUAL_MACHINE_NAME}.service"

echo "Provisioned ${VIRTUAL_MACHINE_NAME}."
