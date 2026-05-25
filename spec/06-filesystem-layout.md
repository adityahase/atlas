# On-Host Filesystem Layout

Everything Atlas puts on a metal node lives under `/var/lib/atlas/`. Nothing
else.

```
/var/lib/atlas/
├── images/
│   └── ubuntu-24.04/
│       ├── vmlinux-6.1.141            # kernel binary, immutable per image
│       ├── ubuntu-24.04.ext4          # pristine rootfs, immutable per image
│       └── sha256sums                 # written at sync time, used to verify
│
├── vms/
│   ├── vm-001/
│   │   ├── vmconfig.json              # Firecracker --config-file
│   │   ├── rootfs.ext4                # per-VM mutable rootfs
│   │   ├── network.env                # TAP_DEV, VM_IPV6, etc.
│   │   └── log/
│   │       └── firecracker.log        # stdout + stderr of firecracker
│   ├── vm-002/
│   │   └── ...
│   └── ...
│
├── run/
│   ├── vm-001.sock                    # Firecracker API unix socket
│   ├── vm-002.sock
│   └── ...
│
└── bin/                               # Atlas helper scripts (laid down by bootstrap)
    ├── atlas-vm-postup
    └── atlas-vm-postdown
```

## Conventions

- **Mode 0700** on `/var/lib/atlas/` and every immediate subdirectory. Root-only.
- **One directory per VM**, named by `vm_name`. The directory is the VM's
  identity on disk — listing `vms/` is a quick way to inventory.
- **Logs go inside the VM dir**, not into `/var/log/`. Easier to clean up;
  easier to ship in one tarball when debugging.
- **API sockets go in `/run`** under the Atlas root, not `/var/run/firecracker/`.
  We don't share the path with anything else.
- **Images are read-only after sync.** Provisioning a VM `cp`'s the image rootfs
  into the VM dir. We don't `mount --bind` or use overlayfs in this iteration —
  the disk overhead (~600MB per VM, ext4 sparse) is fine for a building block.

## Helper scripts

`atlas-vm-postup` and `atlas-vm-postdown` are short shell scripts (under
60 lines each). They are laid down by **bootstrap**, not by VM provisioning,
so a host reboot can run them without any new state from Atlas. Both take a
single argument, the VM name, and read `/var/lib/atlas/vms/$1/network.env`.

Contents are specified in [05-networking.md](./05-networking.md).

## What if `/var/lib/atlas/` runs out of space?

A future problem. The operator gets an SSH alarm, runs `df`, deletes archived
VM directories by hand, or provisions a new node and migrates. This iteration
does not include a janitor.

## What about `/var/lib/atlas/vms/<archived>`?

When we `Delete` a VM we `rm -rf` the directory. The Frappe-side row is
renamed to `<vm_name>-archived-<epoch>` and kept. So the on-disk directory
name is always the **live** name; archived VMs leave no on-disk trace.
