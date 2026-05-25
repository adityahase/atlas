# Roadmap — what we're deliberately deferring

This iteration is a building block. The list below is everything we've
*chosen* not to do, paired with the cheapest path to add it later. If a
deferred item would force a schema or interface change we can't backfill,
it's flagged **breaking**.

## Next iteration (probably immediate)

- **Unprivileged user on the metal node.** Move from `root` to an `atlas`
  user with NOPASSWD sudo on a tight allowlist (`firecracker`, `ip`, `nft`,
  `systemctl`, `mkfs.ext4`, `mount`/`umount`, `truncate`, `cp`, `install`).
  Then drop sudo for the firecracker binary in favor of the **jailer**.
  Not breaking — the `Metal Provider.ssh_private_key` and the SSH wrapper
  are the only touch points.

- **A small CLI** (`atlas vm ls`, `atlas vm start`, etc.) that talks to the
  same Frappe REST API the buttons use. Pure additive.

- **Host key pinning.** Capture the host key on first SSH after droplet
  creation, store it on `Metal Node`, refuse `AutoAddPolicy` after that.
  Adds one field on `Metal Node`. Not breaking.

- **Bare metal provider.** A second `provider_type` that creates rows by
  hand (IP + SSH key + region as form input) instead of calling DO. The
  provider abstraction in [01-architecture.md](./01-architecture.md) was
  designed for this. Not breaking.

## After the building block is solid

- **Custom images.** A `VM Image Build` DocType that builds an ext4 from a
  Dockerfile or debootstrap recipe, pushes to a registry/bucket, and points
  `VM Image` at it. Likely additive — the `VM Image` DocType already
  treats kernel/rootfs URLs as opaque.

- **Overlayfs-backed rootfs.** Each VM gets `lower=image.ext4` plus a thin
  upper. Reduces per-VM disk by ~10x. Requires changing the `provision`
  flow but not the DocType schema.

- **Snapshots.** Firecracker supports diff snapshots and resumes. Will need
  a `VM Snapshot` DocType and changes to the lifecycle state machine
  (a `Suspended` state). **Breaking** for code paths that assume the
  3-state Pending/Running/Stopped model — keep that in mind when writing
  status checks now (don't `if status != "Running": treat as Stopped`,
  always handle each value).

- **IPv4 egress for guests.** A NAT64/DNS64 deployment on each metal node,
  or a separate egress gateway VM. Out of scope here, but worth flagging:
  guests in this iteration can't `apt update` from IPv4-only mirrors.
  Mitigation: the Ubuntu 24.04 archives are reachable over IPv6 from most
  DO regions.

- **Health checks and reconciliation.** A scheduled job that SSH's into
  every active node, runs `systemctl is-active atlas-vm@<name>` for each
  VM, and updates `Virtual Machine.status` to match. Pure additive.

- **Metrics.** `firecracker --metrics-path` writes a JSON file per VM
  every Nth second. Ship those to wherever metrics live. Additive.

- **Console access.** A signed URL that proxies to the guest's serial
  console (Firecracker exposes it over the API socket). Needs a small web
  service. Additive.

## After Atlas has real consumers

- **Quotas and ownership.** The "Site/Bench/IAM/Billing" layer above Atlas
  adds a `team` field on `Virtual Machine` and `Metal Node`. Atlas itself
  stays unaware. Additive.

- **Placement / scheduling.** Today the operator picks the node. Eventually
  some upper layer will pick for them based on free capacity. Atlas gains a
  `Metal Node.capacity_*` set of computed fields. Additive.

- **High availability.** Multi-AZ replicas, snapshot-and-restore failover.
  Significant work; will need its own spec.

## What we will *not* do regardless

- Build our own hypervisor. Firecracker is the building block.
- Build a portal. Desk plus the eventual CLI cover every need we have.
- Adopt Kubernetes. The point of this stack is to avoid that level of
  complexity for our use case.
- Multi-tenant secrets in this app. Site-level secrets belong in the
  Site/Bench layer.

## Versioning the spec

When something in this folder is meaningfully wrong (not just incomplete),
update the file in place and add a one-line note at the bottom of
`09-roadmap.md` under a "Changes" heading with the date and a sentence
about what flipped. The git log is the canonical history; this list is the
human-readable summary.

### Changes

- _none yet — this is v0._
