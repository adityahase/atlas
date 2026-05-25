# VM Lifecycle

The lifecycle is intentionally small: **provision, start, stop, delete**. There
is no resize, no migrate, no snapshot, no clone. Changing CPU/RAM means
deleting the VM and creating a new one.

## States

```
                  (Create form, submit)
                          |
                          v
                       Pending
                          |
              (provision job picks it up)
                          |
                          v
                    Provisioning
                          |
              +-----------+-----------+
              |                       |
              v                       v
           Running                 Failed
              |                       ^
   (Stop)     |                       | (Provision failure)
              v                       |
           Stopped                    |
              |                       |
   (Start)    +-------> Running       |
                          |           |
   (Delete from any state |           |
    above leads here)     |           |
                          v           v
                       Deleting       (operator inspects and Archives)
                          |
                          v
                       Archived
```

`Archived` is terminal. We keep the row for history. Re-using the `vm_name` is
allowed because the archived row's name is suffixed with `-archived-{timestamp}`
on archive (rename).

## Provisioning a VM

Triggered by `Virtual Machine.insert()` with `status = Pending`, or by clicking
"Provision" on a row that ended up in `Failed`.

Background job: `atlas.virtual_machine.tasks.provision(vm_name)`.

Steps (each one a separate `Metal Command`):

1. **Reserve networking** (server-side, no SSH yet)
   - Allocate IPv6 from the node's /64. See [05-networking.md](./05-networking.md).
   - Derive MAC from the last 4 octets of the IPv4 representation we don't
     have — instead, generate a stable MAC: `06:00` prefix + 4 bytes from
     `sha1(vm_name)[:4]`. Save on the doc.
   - Derive tap device name: `tap-{first 12 chars of vm_name with dashes
     stripped}`. Linux limit is 15. Save on the doc.

2. **Verify image is on node** (SSH)
   - Check `/var/lib/atlas/images/{image_name}/{kernel_filename}` and
     `.../{rootfs_filename}` exist with matching checksums. If not, call
     `VM Image.sync_to_node(node)` first, which is its own series of commands.

3. **Create VM directory** (SSH)
   ```
   install -d -m 0700 /var/lib/atlas/vms/{vm_name}
   install -d -m 0700 /var/lib/atlas/vms/{vm_name}/log
   ```

4. **Create per-VM rootfs** (SSH)
   - `cp /var/lib/atlas/images/{image}/{rootfs_filename} /var/lib/atlas/vms/{vm_name}/rootfs.ext4`
     Plain copy. No CoW, no overlayfs for this iteration — simple is the goal.
   - `truncate -s {disk_gb}G /var/lib/atlas/vms/{vm_name}/rootfs.ext4`
   - `e2fsck -fy /var/lib/atlas/vms/{vm_name}/rootfs.ext4 || true`
   - `resize2fs /var/lib/atlas/vms/{vm_name}/rootfs.ext4`

5. **Inject the user's SSH key into the rootfs** (SSH)
   - `mount -o loop /var/lib/atlas/vms/{vm_name}/rootfs.ext4 /mnt/vmroot-{vm_name}`
   - `mkdir -p /mnt/vmroot-{vm_name}/root/.ssh && chmod 700`.
   - `echo '{ssh_public_key}' > /mnt/vmroot-{vm_name}/root/.ssh/authorized_keys && chmod 600`.
   - `umount /mnt/vmroot-{vm_name}`.

6. **Write vmconfig.json** (SSH, atomic write to a tempfile then `mv`)

   We use Firecracker's `--config-file` mode (see getting-started, end of the
   doc) — fewer moving parts than driving the API socket. The systemd unit
   passes `--config-file`.

   ```json
   {
     "boot-source": {
       "kernel_image_path": "/var/lib/atlas/images/{image}/{kernel_filename}",
       "boot_args": "console=ttyS0 reboot=k panic=1"
     },
     "drives": [
       {
         "drive_id": "rootfs",
         "path_on_host": "/var/lib/atlas/vms/{vm_name}/rootfs.ext4",
         "is_root_device": true,
         "is_read_only": false
       }
     ],
     "network-interfaces": [
       {
         "iface_id": "eth0",
         "guest_mac": "{mac_address}",
         "host_dev_name": "{tap_device}"
       }
     ],
     "machine-config": {
       "vcpu_count": {vcpus},
       "mem_size_mib": {memory_mb}
     }
   }
   ```

7. **Set up host networking for this VM** (SSH) — see [05-networking.md](./05-networking.md).

8. **Enable and start the systemd unit** (SSH)
   ```
   systemctl enable --now atlas-vm@{vm_name}.service
   ```

9. **Status → Running**, `last_started_at = now()`.

If any step fails, status → `Failed`, no rollback of partial host state in
this iteration. The operator clicks `Delete` to clean up, then re-creates.

## The systemd unit template

Installed at bootstrap time. One template, parameterized by VM name.

`/etc/systemd/system/atlas-vm@.service`:

```ini
[Unit]
Description=Atlas Firecracker VM %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/rm -f /var/lib/atlas/run/%i.sock
ExecStart=/usr/local/bin/firecracker \
    --api-sock /var/lib/atlas/run/%i.sock \
    --config-file /var/lib/atlas/vms/%i/vmconfig.json
ExecStartPost=/usr/local/bin/atlas-vm-postup %i
ExecStopPost=/usr/local/bin/atlas-vm-postdown %i
StandardOutput=append:/var/lib/atlas/vms/%i/log/firecracker.log
StandardError=append:/var/lib/atlas/vms/%i/log/firecracker.log
Restart=always
RestartSec=5s
KillMode=process

[Install]
WantedBy=multi-user.target
```

Notes:

- `Restart=always` — "Keep them running". If Firecracker exits for any reason
  (guest panic, OOM, segfault), systemd restarts it. Operator-initiated `stop`
  uses `systemctl stop`, which inhibits `Restart=always` for that invocation.
- `--api-sock` is still passed so post-boot operations (future iteration) can
  drive the VM. We don't use it during boot — `--config-file` is enough.
- `atlas-vm-postup` and `atlas-vm-postdown` are two tiny scripts laid down by
  bootstrap. They configure the tap device and nft rules **inside the unit's
  lifecycle** so a host reboot brings VMs back up with networking intact.
  Details in [05-networking.md](./05-networking.md).

## Start / Stop / Restart

These are thin wrappers:

- Start: `systemctl start atlas-vm@{vm_name}`. Then `last_started_at = now()`.
- Stop: `systemctl stop atlas-vm@{vm_name}`. Then `last_stopped_at = now()`.
- Restart: `systemctl restart atlas-vm@{vm_name}`. Updates both timestamps.

Each one is a single `Metal Command`. The status field on the doc is updated
optimistically; we do not poll the host to verify.

## Delete

1. `systemctl disable --now atlas-vm@{vm_name}` — stops and removes from
   `multi-user.target`.
2. `atlas-vm-postdown {vm_name}` — explicit network teardown in case systemd
   didn't fire it (e.g. if the unit was already failed).
3. `rm -rf /var/lib/atlas/vms/{vm_name}`.
4. Rename the doc: `vm_name = "{vm_name}-archived-{epoch}"`, `status = Archived`.

We do not delete the doc. History is more valuable than the row.

## Host reboot recovery

Because all VMs are systemd units with `WantedBy=multi-user.target`, a host
reboot brings them all back. `atlas-vm-postup` re-creates the tap device and
nft rules from values it reads out of `vmconfig.json` + a tiny sidecar file
`/var/lib/atlas/vms/{vm_name}/network.env` we drop at provision time
(containing IPV6, IPV6_GATEWAY, TAP_DEV). The DB does not need to be
consulted on host reboot.
