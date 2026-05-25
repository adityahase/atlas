# Metal Node Bootstrap

A metal node starts as a vanilla Ubuntu 24.04 droplet. Bootstrap is the
process that turns it into a Firecracker host that Atlas can drive.

## Prerequisites enforced at provider level

When we create the droplet via the DO API, we must request a size that
supports **KVM/nested virtualization**. As of writing, DO supports KVM on
"Premium Intel" and "Premium AMD" droplets (e.g. `s-2vcpu-4gb-intel`,
`s-4vcpu-8gb-intel`). We default to `s-2vcpu-4gb-intel`.

We also request:

- Image: `ubuntu-24-04-x64`.
- `ipv6: true`.
- An SSH key pre-loaded so the first SSH works.

If the droplet does not have `/dev/kvm` after first boot, bootstrap fails
loudly and the `Metal Node.status` flips to `Broken`.

## The script

The bootstrap is a **single shell script** generated server-side from a Jinja
template (so we can interpolate versions and config) and uploaded as a
`Metal Command`. It is idempotent — running it twice is a no-op the second
time.

We do not use Ansible, pyinfra, cloud-init, or any other framework for this
iteration. One shell script is enough and is trivial to read.

### What the script does, in order

1. **Pre-flight**
   - `[ -r /dev/kvm ]` and `[ -w /dev/kvm ]` — error out if missing.
   - `apt-get update`.
2. **Install packages** (idempotent via `apt-get install -y`):
   - `nftables` — firewall.
   - `iproute2` — already present, but pin it.
   - `curl`, `ca-certificates` — used by the script itself.
   - `e2fsprogs` — for `mkfs.ext4`, `e2fsck`, `resize2fs`.
   - `squashfs-tools` — for unpacking the CI rootfs.
   - `jq` — only for shell pipelines we'll write later. Keep it.
   - That's it. No Docker, no Python, no Go, no agent.
3. **Install firecracker**
   - Detect arch (`uname -m`).
   - Download `firecracker-${VERSION}-${ARCH}.tgz` from the GitHub releases of
     `firecracker-microvm/firecracker` to `/tmp`. Verify checksum (we hard-code
     a known release version + checksum in the script template — see below).
   - Extract and install to `/usr/local/bin/firecracker`. `chmod +x`.
   - Verify: `firecracker --version` records into the `Metal Command` stdout;
     the caller parses it and writes it back to
     `Metal Node.firecracker_version`.
4. **Enable IPv6 forwarding** (see networking doc for details)
   - Write `/etc/sysctl.d/60-atlas.conf`:
     ```
     net.ipv6.conf.all.forwarding = 1
     net.ipv6.conf.default.forwarding = 1
     net.ipv6.conf.all.proxy_ndp = 1
     ```
   - `sysctl --system`.
5. **Create the nftables scaffold**
   - `nft add table inet atlas` (idempotent — wrap in `nft list table inet atlas 2>/dev/null || nft add ...`).
   - `nft 'add chain inet atlas forward { type filter hook forward priority filter; policy accept; }'`.
   - Per-VM rules are added at VM-create time, not here.
6. **Lay down directories**
   - `mkdir -p /var/lib/atlas/{images,vms,run}`.
   - `chmod 700 /var/lib/atlas`.
7. **Install the systemd unit template**
   - Write `/etc/systemd/system/atlas-vm@.service` (see [VM Lifecycle](./04-vm-lifecycle.md)).
   - `systemctl daemon-reload`.
8. **Record kernel version**
   - `uname -r` into stdout; caller writes it back into `Metal Node.kernel_version`.

### Idempotency

- All `apt-get install` are idempotent.
- File writes use `install -m 0644 -T` (atomic, deterministic).
- `nft add table` / `add chain` are wrapped in `... || true`.
- `mkdir -p` and `systemctl daemon-reload` are naturally idempotent.

### Where it lives in the app

```
atlas/atlas/scripts/bootstrap.sh.j2     <- the Jinja template
atlas/atlas/atlas/doctype/metal_node/metal_node.py
    .bootstrap()
        - renders the template
        - opens an SSH session
        - uploads the script to /root/atlas-bootstrap.sh
        - chmod +x
        - runs it under `bash -x` so we have a full trace in stderr
        - captures stdout/stderr into a Metal Command
        - on exit_code == 0: parses the version lines and flips status to Active
        - on exit_code != 0: flips status to Broken
```

The script template is short — target under 150 lines including comments. If
it grows past that, we have over-scoped.

### Pinned versions

Pinned in the Jinja template (not user-configurable yet):

- `FIRECRACKER_VERSION = "v1.13.0"` (update to whatever is current at time of
  implementation; bump together with image checksums).
- Architecture: `x86_64` only for this iteration. `aarch64` deferred.

Bump the version by editing the template and re-running `Bootstrap` on
every node. The script is idempotent, so re-running is safe.

### Failure modes

| Failure                          | Resulting status   | Operator action                |
| -------------------------------- | ------------------ | ------------------------------ |
| SSH never comes up               | `Pending`          | Investigate droplet on DO.     |
| `/dev/kvm` missing               | `Broken`           | Wrong droplet size — recreate. |
| `apt-get` fails                  | `Broken`           | Re-run Bootstrap.              |
| Firecracker download fails       | `Broken`           | Re-run Bootstrap.              |
| Checksum mismatch                | `Broken`           | Bug in the spec — file an issue.|

There is no automatic retry. The operator clicks `Bootstrap` again. That's the
escape hatch and it's the same code path.
