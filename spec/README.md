# Atlas — Specification

Atlas is a Frappe app for managing Firecracker microVMs on metal nodes. It is
the lowest layer of a Frappe hosting platform. Sites, benches, IAM, and billing
are out of scope and will be built as separate apps on top.

## Goals

- Track metal nodes (the physical/virtual machines that host microVMs).
- Track microVMs that run on those metal nodes.
- Bootstrap a metal node so it can host Firecracker microVMs.
- Spawn, start, stop, and delete Ubuntu 24.04 Firecracker microVMs.
- Drive everything over SSH from the Frappe site; record every command.
- Give each VM IPv6-only public connectivity.

## Non-goals (this iteration)

- No site, bench, app, database, or workload management — that is the next layer.
- No users, teams, roles, billing, quotas.
- No CLI. We will build it later on top of the same Frappe APIs.
- No private networking, no overlay, no VPC, no IPv4 to the guest.
- No jailer, no unprivileged user, no SELinux/AppArmor. Root everywhere.
- No image building pipeline. We download the Firecracker CI image and use it.
- No snapshots, no live migration, no high availability.
- No autoscaling, no scheduling, no placement logic. Operator picks the node.
- No metrics, no log streaming, no alerting. `journalctl` is enough.
- No web/portal UI. Desk is the UI.

## Operating principles

1. **Desk is the UI.** Every operation is a DocType, a button on a DocType, or
   a server-side method on a DocType. No custom pages, no React, no portal.
2. **The site is the source of truth.** A metal node is a cache; we can rebuild
   its on-disk state from the Frappe database. The reverse is not true: we do
   not scrape state back from the node.
3. **Commands are first-class.** Every SSH invocation is persisted as a
   `Metal Command` document with stdout, stderr, exit code, and timing. There
   is no "fire and forget".
4. **One node per VM, one VM per node row.** No scheduling, no rebalancing.
   The operator picks the metal node when creating a VM.
5. **Simple over flexible.** Fixed Ubuntu 24.04 rootfs, fixed kernel from
   Firecracker CI, fixed subnet allocation scheme, fixed systemd unit template.
6. **Few dependencies.** Standard library + Frappe + `paramiko` for SSH +
   `python-digitalocean` for the DO API. Nothing else server-side. On the
   metal node: `firecracker`, `systemd`, `iproute2`, `nftables`, `curl`. No
   agent process on the node.

## Read this in order

1. [Architecture](./01-architecture.md) — components, data flow, where state lives.
2. [DocTypes](./02-doctypes.md) — every DocType with fields and Desk wireframes.
3. [Metal Node Bootstrap](./03-metal-node-bootstrap.md) — how a fresh DO droplet
   becomes a Firecracker host.
4. [VM Lifecycle](./04-vm-lifecycle.md) — create / start / stop / delete.
5. [Networking](./05-networking.md) — IPv6-only routing and allocation.
6. [Filesystem Layout](./06-filesystem-layout.md) — what lives where on a node.
7. [SSH & Commands](./07-ssh-and-commands.md) — the execution model.
8. [Images](./08-images.md) — kernel + rootfs storage.
9. [Roadmap](./09-roadmap.md) — what changes after this milestone.
