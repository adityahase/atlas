#!/bin/bash
# Download a kernel + rootfs pair into /var/lib/atlas/images/$IMAGE_NAME/.
# Convert the squashfs rootfs into a pristine ext4 of $DEFAULT_DISK_GB.
# Install the in-guest network unit so VMs come up with static IPv6.
# Idempotent: if files exist with matching checksums, exit early.
#
# Inputs (environment variables):
#   IMAGE_NAME         - directory name under /var/lib/atlas/images
#   KERNEL_URL         - HTTPS URL of the uncompressed vmlinux
#   KERNEL_FILENAME    - destination filename, e.g. vmlinux-6.1.141
#   KERNEL_SHA256      - hex digest of the kernel
#   ROOTFS_URL         - HTTPS URL of the source squashfs
#   ROOTFS_FILENAME    - destination ext4 filename, e.g. ubuntu-24.04.ext4
#   ROOTFS_SHA256      - hex digest of the *source squashfs*, not the ext4
#   DEFAULT_DISK_GB    - size of the pristine ext4
#   GUEST_NETWORK_UNIT - path on the server to the guest atlas-network.service
#                        (uploaded by the caller before running this script)

set -euo pipefail

: "${IMAGE_NAME:?required}"
: "${KERNEL_URL:?required}"
: "${KERNEL_FILENAME:?required}"
: "${KERNEL_SHA256:?required}"
: "${ROOTFS_URL:?required}"
: "${ROOTFS_FILENAME:?required}"
: "${ROOTFS_SHA256:?required}"
: "${DEFAULT_DISK_GB:?required}"
: "${GUEST_NETWORK_UNIT:?required}"

image_directory="/var/lib/atlas/images/${IMAGE_NAME}"
sudo install -d -m 0700 "$image_directory"

# 1. Kernel.
kernel_path="${image_directory}/${KERNEL_FILENAME}"
if [ -f "$kernel_path" ] && echo "${KERNEL_SHA256}  ${kernel_path}" | sudo sha256sum -c --status -; then
    echo "Kernel already present and matches checksum."
else
    sudo rm -f "${kernel_path}.part"
    sudo curl -fsSL --output "${kernel_path}.part" "$KERNEL_URL"
    echo "${KERNEL_SHA256}  ${kernel_path}.part" | sudo sha256sum -c -
    sudo mv "${kernel_path}.part" "$kernel_path"
fi

# 2. Rootfs.
rootfs_path="${image_directory}/${ROOTFS_FILENAME}"
if [ -f "$rootfs_path" ]; then
    echo "Rootfs already built. Skipping."
    exit 0
fi

squashfs_path="/tmp/atlas-${IMAGE_NAME}.squashfs"
extracted_directory="/tmp/atlas-${IMAGE_NAME}-rootfs"
sudo rm -f "${squashfs_path}.part" "$squashfs_path"
sudo rm -rf "$extracted_directory"

sudo curl -fsSL --output "${squashfs_path}.part" "$ROOTFS_URL"
echo "${ROOTFS_SHA256}  ${squashfs_path}.part" | sudo sha256sum -c -
sudo mv "${squashfs_path}.part" "$squashfs_path"

sudo unsquashfs -d "$extracted_directory" "$squashfs_path"

# 3. Install the guest network unit and a placeholder env file.
sudo install -d -m 0755 "${extracted_directory}/etc/systemd/system"
sudo install -d -m 0755 "${extracted_directory}/etc/systemd/system/multi-user.target.wants"
sudo install -m 0644 "$GUEST_NETWORK_UNIT" "${extracted_directory}/etc/systemd/system/atlas-network.service"
sudo ln -sf /etc/systemd/system/atlas-network.service \
    "${extracted_directory}/etc/systemd/system/multi-user.target.wants/atlas-network.service"
echo "" | sudo install -m 0644 /dev/stdin "${extracted_directory}/etc/atlas-network.env"

# 4. Build the ext4.
sudo chown -R root:root "$extracted_directory"
sudo truncate -s "${DEFAULT_DISK_GB}G" "${rootfs_path}.part"
sudo mkfs.ext4 -q -d "$extracted_directory" -F "${rootfs_path}.part"
sudo mv "${rootfs_path}.part" "$rootfs_path"

sudo rm -rf "$extracted_directory" "$squashfs_path"

echo "Image ${IMAGE_NAME} ready."
