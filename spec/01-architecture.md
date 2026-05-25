# Architecture

## The picture

```
                +----------------------------------+
                |    Atlas (Frappe site)           |
                |    atlas.local                   |
                |                                  |
                |  DocTypes:                       |
                |   - Metal Provider               |
                |   - Metal Node                   |
                |   - Virtual Machine              |
                |   - Metal Command                |
                |   - VM Image                     |
                |                                  |
                |  Desk = the UI                   |
                +-----------------+----------------+
                                  |
                       SSH (root, key-based)
                                  |
              +-------------------+-------------------+
              |                                       |
   +----------v----------+                +-----------v---------+
   |   Metal Node A      |                |   Metal Node B      |
   |   (DO droplet)      |                |   (DO droplet)      |
   |                     |                |                     |
   |  /var/lib/atlas/    |                |  /var/lib/atlas/    |
   |    images/          |                |    images/          |
   |    vms/<vm-id>/     |                |    vms/<vm-id>/     |
   |                     |                |                     |
   |  systemd:           |                |  systemd:           |
   |   atlas-vm@<id>     |                |   atlas-vm@<id>     |
   |                     |                |                     |
   |  Firecracker procs  |                |  Firecracker procs  |
   |  (one per VM)       |                |  (one per VM)       |
   +---------------------+                +---------------------+
```

## Components

### Atlas (this app)

A Frappe app installed on `atlas.local`. It owns the database of nodes and VMs,
exposes Desk forms for the operator, and runs background jobs that SSH into
metal nodes to make changes.

There is no Atlas agent on the metal node. All state changes on the node are
the result of one or more SSH commands issued from the Frappe site, with the
final state described as files in `/var/lib/atlas/` and a systemd unit.

### Metal Provider

For this iteration there is exactly one provider: DigitalOcean. The
`Metal Provider` DocType stores the DO API token and default region/size/image.
The provider knows how to:

- Create a droplet — returns IPv4 and IPv6.
- Delete a droplet.
- List droplets (for reconciliation / debugging).

The provider abstraction exists so a future iteration can swap DO for bare
metal without touching the rest of the code. It is not designed for
multi-cloud; only one provider type is implemented.

### Metal Node

A `Metal Node` document represents one droplet. It is created by clicking
"Provision" on a `Metal Provider`, which:

1. Calls the DO API to create a droplet.
2. Inserts a `Metal Node` record with the returned IPs.
3. Waits for SSH to come up.
4. Runs the bootstrap (see [03-metal-node-bootstrap.md](./03-metal-node-bootstrap.md)).
5. Marks the node `Active`.

### Virtual Machine

A `Virtual Machine` document represents one Firecracker microVM running on a
metal node. The operator picks the node when creating the VM. The lifecycle
buttons on the form (`Start`, `Stop`, `Delete`) translate to SSH commands on
that node.

### Metal Command

Every SSH invocation made by Atlas is persisted. The `Metal Command` document
records the node it ran against, the VM it was for (if any), the command, the
stdout/stderr, exit code, start/end time, and the user who triggered it. This
is our audit log and our debugging tool.

### VM Image

Right now there is one image: Ubuntu 24.04 from Firecracker CI (kernel +
squashfs/ext4 rootfs). `VM Image` is a DocType because we want a stable
reference from `Virtual Machine` and because the next iteration will add
more images. The image bytes live on each metal node under `/var/lib/atlas/images/`.

## Data flow: creating a VM

```
operator clicks "Create VM" on Virtual Machine form
      |
      v
Virtual Machine.insert()  -> status: Pending
      |
      v
enqueue background job: atlas.vm.provision(vm_name)
      |
      v
SSH to metal node:
  1. mkdir -p /var/lib/atlas/vms/<vm-id>
  2. cp -l image rootfs into the VM dir          (hardlink, see images doc)
  3. write vmconfig.json
  4. write systemd unit (or reload template)
  5. set up tap device + nft rules               (see networking doc)
  6. systemctl enable --now atlas-vm@<vm-id>
      |
      v
each step = one Metal Command record
      |
      v
Virtual Machine.status = Running
```

## What lives where

| State                         | Where it lives                         | Authoritative? |
| ----------------------------- | -------------------------------------- | -------------- |
| Node IPs, sizes, providers    | Frappe DB                              | Yes            |
| VM specs (vCPU, RAM, disk)    | Frappe DB                              | Yes            |
| Which VM runs on which node   | Frappe DB                              | Yes            |
| IPv6 address assignments      | Frappe DB                              | Yes            |
| Command history               | Frappe DB                              | Yes            |
| Image bytes                   | Each metal node `/var/lib/atlas/images`| No (cache)     |
| `vmconfig.json`, rootfs file  | Each metal node `/var/lib/atlas/vms/`  | No (cache)     |
| systemd units                 | Each metal node `/etc/systemd/system/` | No (cache)     |
| Running Firecracker processes | Each metal node                        | No (cache)     |

"No (cache)" means: if we lose it, we can recreate it from the Frappe DB. We do
not parse it back to update Frappe.
