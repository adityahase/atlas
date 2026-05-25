# Images

One image for this iteration: **Ubuntu 24.04** from Firecracker CI. The image
is the (kernel, rootfs) pair the Firecracker docs reference in their
[getting started guide](../llm/references/firecracker/docs/getting-started.md).

## Image record

A `VM Image` document (see [02-doctypes.md](./02-doctypes.md)) carries:

- URL of the kernel binary.
- URL of the source squashfs rootfs.
- SHA-256 of each.
- Filenames to store them under on the host.
- A `default_disk_gb` used when a VM doesn't override it.

Image bytes never live in the Frappe DB. They live as files on each metal
node, and as a URL anywhere else.

## Sync to a node

Triggered by:

- A button on `VM Image` (sync to every active node).
- A button on `Metal Node` (sync one image, or all active images).
- Implicitly by VM provisioning, if the image is missing.

The sync runs as a series of SSH commands on the target node:

1. `install -d -m 0700 /var/lib/atlas/images/{image_name}/`.

2. If the kernel file is already present **and** its sha256 matches: skip.
   Else:
   ```bash
   curl -fsSL --output /var/lib/atlas/images/{image_name}/{kernel_filename}.part \
       '{kernel_url}'
   echo '{kernel_sha256}  /var/lib/atlas/images/{image_name}/{kernel_filename}.part' \
       | sha256sum -c -
   mv /var/lib/atlas/images/{image_name}/{kernel_filename}.part \
      /var/lib/atlas/images/{image_name}/{kernel_filename}
   ```

3. For the rootfs: same logic, but with **squashfs → ext4 conversion** on the
   host because Firecracker wants an ext4 rootfs:

   ```bash
   curl -fsSL --output /tmp/{image_name}.squashfs.part '{rootfs_url}'
   echo '{rootfs_sha256}  /tmp/{image_name}.squashfs.part' | sha256sum -c -
   mv /tmp/{image_name}.squashfs.part /tmp/{image_name}.squashfs

   rm -rf /tmp/{image_name}-squashfs-root
   unsquashfs -d /tmp/{image_name}-squashfs-root /tmp/{image_name}.squashfs

   # Drop the in-guest stable assets:
   #   - the one-shot network unit (same content for every VM ever made from
   #     this image)
   #   - the placeholder /etc/atlas-vm.env (overwritten per-VM at provision)
   install -m 0644 /dev/stdin /tmp/{image_name}-squashfs-root/etc/systemd/system/atlas-net.service <<'EOF'
   [Unit]
   Description=Atlas VM Static IPv6
   After=network-pre.target
   Before=network.target
   [Service]
   Type=oneshot
   EnvironmentFile=/etc/atlas-vm.env
   ExecStart=/usr/sbin/ip -6 addr add ${VM_IPV6}/128 dev eth0
   ExecStart=/usr/sbin/ip link set eth0 up
   ExecStart=/usr/sbin/ip -6 route add default via fe80::1 dev eth0
   ExecStart=/bin/sh -c 'echo "nameserver 2606:4700:4700::1111" > /etc/resolv.conf'
   RemainAfterExit=yes
   [Install]
   WantedBy=multi-user.target
   EOF
   ln -sf /etc/systemd/system/atlas-net.service \
          /tmp/{image_name}-squashfs-root/etc/systemd/system/multi-user.target.wants/atlas-net.service
   touch /tmp/{image_name}-squashfs-root/etc/atlas-vm.env

   # Build the ext4
   sudo chown -R root:root /tmp/{image_name}-squashfs-root
   truncate -s {default_disk_gb}G /var/lib/atlas/images/{image_name}/{rootfs_filename}
   mkfs.ext4 -d /tmp/{image_name}-squashfs-root -F /var/lib/atlas/images/{image_name}/{rootfs_filename}

   rm -rf /tmp/{image_name}-squashfs-root /tmp/{image_name}.squashfs
   ```

4. Write a small marker file:
   ```bash
   printf '%s  %s\n%s  %s\n' \
       '{kernel_sha256}' '{kernel_filename}' \
       '{rootfs_sha256}'  '{rootfs_filename}' \
       > /var/lib/atlas/images/{image_name}/sha256sums
   ```

## Per-VM rootfs creation

When a VM is provisioned (see [04-vm-lifecycle.md](./04-vm-lifecycle.md)):

1. `cp /var/lib/atlas/images/{image}/{rootfs_filename} \
       /var/lib/atlas/vms/{vm_name}/rootfs.ext4`
2. `truncate -s {disk_gb}G /var/lib/atlas/vms/{vm_name}/rootfs.ext4`
3. `e2fsck -fy ... ; resize2fs ...` to grow the FS.
4. `mount -o loop` to:
   - drop the user's SSH key into `/root/.ssh/authorized_keys`,
   - write `/etc/atlas-vm.env` with `VM_IPV6={ipv6_address}`,
5. `umount`.

The atlas-net.service unit is already baked into the image during sync, so
all we touch per-VM is the env file and the SSH key.

## Why plain copy and not overlayfs

For this iteration:

- One copy of the rootfs per VM is ~600MB–4GB. On `s-2vcpu-4gb-intel` (80 GB
  SSD) that's room for ~20 VMs of 4 GB each. Plenty for the building block.
- Overlayfs adds complexity: a writable upper layer per VM, careful unmount
  on stop, more failure modes during host reboot.
- We can reach for it later when density matters.

## Why convert squashfs → ext4 host-side instead of shipping ext4

We could host pre-built ext4 images on our own bucket. We deliberately don't,
because:

- It adds a build pipeline and a storage cost for the building block.
- The Firecracker CI squashfs is already public and stable for the supported
  releases.
- Conversion on the host is ~10 seconds per node per image — a one-time cost.

When we add custom images (with extra packages), we'll revisit this. For
this iteration, the squashfs URL is authoritative.

## Verification

Every download is checksummed. A mismatched checksum is a hard failure of
the sync command — the `.part` file is left in place for inspection and the
`Metal Command` records the mismatch in stderr.
