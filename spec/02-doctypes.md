# DocTypes

Five DocTypes. Names use `Atlas` as the module. All are submittable=0,
istable=0 unless stated.

1. [Metal Provider](#metal-provider)
2. [Metal Node](#metal-node)
3. [Virtual Machine](#virtual-machine)
4. [Metal Command](#metal-command)
5. [VM Image](#vm-image)

Defaults that apply to all:

- `autoname` = `field:name` where a meaningful unique field exists, else `hash`.
- `track_changes` = 1.
- Read permission for `System Manager`. No public/guest exposure.

---

## Metal Provider

One row per cloud account. For now only `DigitalOcean` is implemented.

| Field           | Type           | Reqd | Notes                                                |
| --------------- | -------------- | ---- | ---------------------------------------------------- |
| `provider_name` | Data           | Y    | Primary key. e.g. `do-prod`.                         |
| `provider_type` | Select         | Y    | Options: `DigitalOcean`. Single option for now.      |
| `api_token`     | Password       | Y    | DO personal access token.                            |
| `default_region`| Data           | Y    | e.g. `blr1`.                                         |
| `default_size`  | Data           | Y    | e.g. `s-2vcpu-4gb-intel`. Must support nested virt.  |
| `default_image` | Data           | Y    | e.g. `ubuntu-24-04-x64`.                             |
| `ssh_key_id`    | Data           | Y    | DO SSH key fingerprint to inject at droplet create.  |
| `ssh_private_key` | Password (Long Text) | Y | Matching private key. Used by Atlas to SSH in. |
| `is_active`     | Check          |      | Defaults to 1.                                       |

Buttons:

- **Provision Metal Node** — opens a quick prompt for a `node_name`, then
  creates a droplet and a `Metal Node` document. Runs as a background job.
- **Test Connection** — pings the DO API with the token. Shows the account
  slug on success.

### Desk wireframe — Metal Provider form

```
+-----------------------------------------------------------+
| Metal Provider: do-prod                       [Active] [v]|
+-----------------------------------------------------------+
|  Provider Name *      [ do-prod                         ] |
|  Provider Type *      [ DigitalOcean                  v ] |
|  API Token *          [ ************************        ] |
|                                                           |
|  Defaults                                                 |
|  Default Region *     [ blr1                            ] |
|  Default Size *       [ s-2vcpu-4gb-intel               ] |
|  Default Image *      [ ubuntu-24-04-x64                ] |
|  SSH Key ID *         [ 12:34:56:...:ab                 ] |
|  SSH Private Key *    [ -----BEGIN OPENSSH PRIVATE KEY- ] |
|                                                           |
|  [x] Is Active                                            |
|                                                           |
|  [ Test Connection ]    [ Provision Metal Node ]          |
+-----------------------------------------------------------+
```

---

## Metal Node

One row per droplet/host.

| Field             | Type    | Reqd | Notes                                            |
| ----------------- | ------- | ---- | ------------------------------------------------ |
| `node_name`       | Data    | Y    | Primary key. e.g. `metal-blr1-01`.               |
| `provider`        | Link → Metal Provider | Y |                                       |
| `provider_id`     | Data    | Y    | DO droplet id. Read-only.                        |
| `region`          | Data    | Y    | Read-only.                                       |
| `size`            | Data    | Y    | Read-only.                                       |
| `ipv4_address`    | Data    | Y    | Public IPv4 of the host. SSH endpoint.           |
| `ipv6_address`    | Data    | Y    | Public IPv6 of the host. Used as upstream for VMs. |
| `ipv6_subnet`     | Data    | Y    | The /64 routed to this droplet (DO assigns one). |
| `status`          | Select  | Y    | `Pending`, `Bootstrapping`, `Active`, `Draining`, `Broken`, `Archived`. |
| `firecracker_version` | Data |     | Filled by bootstrap.                             |
| `kernel_version`  | Data    |      | `uname -r` on the host. Filled by bootstrap.     |
| `notes`           | Text    |      | Free-form operator notes.                        |

Buttons:

- **Bootstrap** — runs the bootstrap script over SSH. Idempotent.
- **Run Command** — opens a small dialog to run an ad-hoc shell command on the
  node. Result is captured as a `Metal Command`.
- **Reboot** — issues `systemctl reboot` via SSH. Status flips to `Pending`,
  waits for SSH to come back, flips to `Active`.

Child connections (shown as dashboards on the form):

- Virtual Machines on this node.
- Recent Metal Commands for this node.

### Desk wireframe — Metal Node form

```
+-----------------------------------------------------------------+
| Metal Node: metal-blr1-01                       [Active]    [v] |
+-----------------------------------------------------------------+
|  Node Name *           [ metal-blr1-01                       ]  |
|  Provider *            [ do-prod                       v ]      |
|  Provider ID           [ 412345678              ] (read-only)   |
|  Region                [ blr1                   ] (read-only)   |
|  Size                  [ s-2vcpu-4gb-intel      ] (read-only)   |
|                                                                 |
|  Networking                                                     |
|  IPv4 Address *        [ 139.59.x.y                          ]  |
|  IPv6 Address *        [ 2a03:b0c0:...::1                    ]  |
|  IPv6 Subnet *         [ 2a03:b0c0:...::/64                  ]  |
|                                                                 |
|  Status                                                         |
|  Status *              [ Active                          v ]    |
|  Firecracker Version   [ 1.13.0                ]                |
|  Kernel Version        [ 6.8.0-31-generic      ]                |
|                                                                 |
|  Notes                                                          |
|  [                                                           ]  |
|                                                                 |
|  [ Bootstrap ]  [ Run Command ]  [ Reboot ]                     |
|                                                                 |
|  ── Virtual Machines on this node ─────────────────────────     |
|  vm-001  Running   2 vCPU  2 GB  2a03:b0c0:...:2                |
|  vm-002  Stopped   1 vCPU  1 GB  2a03:b0c0:...:3                |
|                                                                 |
|  ── Recent Commands ───────────────────────────────────────     |
|  2026-05-25 13:01  bootstrap         exit=0    12.3s            |
|  2026-05-25 13:11  start vm-001      exit=0     0.4s            |
+-----------------------------------------------------------------+
```

---

## Virtual Machine

One row per microVM.

| Field             | Type    | Reqd | Notes                                            |
| ----------------- | ------- | ---- | ------------------------------------------------ |
| `vm_name`         | Data    | Y    | Primary key. Slug. e.g. `vm-001`.                |
| `metal_node`      | Link → Metal Node | Y | Where the VM runs. Set at create time.    |
| `image`           | Link → VM Image | Y | Rootfs + kernel pair.                         |
| `vcpus`           | Int     | Y    | Defaults to 1.                                   |
| `memory_mb`       | Int     | Y    | Defaults to 512.                                 |
| `disk_gb`         | Int     | Y    | Rootfs size. Defaults to 4.                      |
| `ipv6_address`    | Data    | Y    | Assigned from the node's /64. See networking doc.|
| `mac_address`     | Data    | Y    | Generated. See networking doc.                   |
| `tap_device`      | Data    | Y    | e.g. `tap-vm001`. Auto-derived.                  |
| `ssh_public_key`  | Long Text | Y  | Injected into the rootfs `authorized_keys`.      |
| `status`          | Select  | Y    | `Pending`, `Provisioning`, `Running`, `Stopped`, `Failed`, `Deleting`, `Archived`. |
| `last_started_at` | Datetime|      |                                                  |
| `last_stopped_at` | Datetime|      |                                                  |

Buttons:

- **Start** — `systemctl start atlas-vm@<vm_name>`.
- **Stop** — `systemctl stop atlas-vm@<vm_name>`.
- **Restart** — stop then start.
- **Delete** — stop, remove `/var/lib/atlas/vms/<vm_name>`, disable unit, tear
  down tap + nft rules, set status to `Archived` (we keep the row).

`metal_node`, `image`, `vcpus`, `memory_mb`, `disk_gb` are immutable after the
VM is first provisioned. To change them, archive and create a new VM. This is
deliberate — it keeps the on-host state derivable from the doc.

### Desk wireframe — Virtual Machine form

```
+-----------------------------------------------------------------+
| Virtual Machine: vm-001                         [Running]   [v] |
+-----------------------------------------------------------------+
|  VM Name *           [ vm-001                                ]  |
|  Metal Node *        [ metal-blr1-01                   v ]      |
|  Image *             [ ubuntu-24.04                    v ]      |
|                                                                 |
|  Resources                                                      |
|  vCPUs *             [ 2     ]                                  |
|  Memory (MB) *       [ 2048  ]                                  |
|  Disk (GB) *         [ 4     ]                                  |
|                                                                 |
|  Networking                                                     |
|  IPv6 Address *      [ 2a03:b0c0:...:2                      ]   |
|  MAC Address *       [ 06:00:00:00:00:02                    ]   |
|  TAP Device *        [ tap-vm001                            ]   |
|                                                                 |
|  Access                                                         |
|  SSH Public Key *    [ ssh-ed25519 AAAA... user@host        ]   |
|                                                                 |
|  Status                                                         |
|  Status *            [ Running                           v ]    |
|  Last Started At     [ 2026-05-25 13:11:02                  ]   |
|  Last Stopped At     [                                      ]   |
|                                                                 |
|  [ Start ]  [ Stop ]  [ Restart ]  [ Delete ]                   |
|                                                                 |
|  ── Recent Commands for this VM ───────────────────────────     |
|  2026-05-25 13:11  systemctl start atlas-vm@vm-001  exit=0      |
|  2026-05-25 13:11  write vmconfig.json              exit=0      |
+-----------------------------------------------------------------+
```

---

## Metal Command

Append-only log of every SSH command Atlas runs.

| Field          | Type    | Reqd | Notes                                          |
| -------------- | ------- | ---- | ---------------------------------------------- |
| `name`         | (autoname `hash`) | | UUID.                                    |
| `metal_node`   | Link → Metal Node | Y |                                          |
| `virtual_machine` | Link → Virtual Machine | | Set when the command is for a VM. |
| `command`      | Code (Bash) | Y | The exact command string sent over SSH.     |
| `status`       | Select  | Y    | `Pending`, `Running`, `Success`, `Failure`.    |
| `exit_code`    | Int     |      | Filled on completion.                          |
| `stdout`       | Code    |      |                                                |
| `stderr`       | Code    |      |                                                |
| `started_at`   | Datetime|      |                                                |
| `ended_at`     | Datetime|      |                                                |
| `duration_ms`  | Int     |      | `ended_at - started_at`, denormalized for sort.|
| `triggered_by` | Link → User | Y| The Frappe user that initiated the action. Defaults to `Administrator` for scheduled jobs. |

Read-only after insert (commands are not editable). Search-enabled fields:
`metal_node`, `virtual_machine`, `status`, `command`.

### Desk wireframe — Metal Command list

```
+-----------------------------------------------------------------+
| Metal Commands                                                  |
+-----------------------------------------------------------------+
|  Node            VM          Command            Status   Dur    |
|  metal-blr1-01   vm-001      systemctl start..  Success   0.4s  |
|  metal-blr1-01   vm-001      mkdir -p /var/...  Success   0.1s  |
|  metal-blr1-01   —           apt-get install... Success  43.2s  |
|  metal-blr1-02   vm-007      curl --unix-soc... Failure   2.1s  |
|  ...                                                            |
+-----------------------------------------------------------------+
```

### Desk wireframe — Metal Command form

```
+-----------------------------------------------------------------+
| Metal Command: 8f3a...                          [Success]       |
+-----------------------------------------------------------------+
|  Metal Node      [ metal-blr1-01                  ]             |
|  Virtual Machine [ vm-001                         ]             |
|  Triggered By    [ aditya@adityahase.com          ]             |
|                                                                 |
|  Command                                                        |
|  ┌───────────────────────────────────────────────────────────┐  |
|  │ systemctl start atlas-vm@vm-001                           │  |
|  └───────────────────────────────────────────────────────────┘  |
|                                                                 |
|  Status          [ Success ]                                    |
|  Exit Code       [ 0       ]                                    |
|  Started At      [ 2026-05-25 13:11:02.114                  ]   |
|  Ended At        [ 2026-05-25 13:11:02.503                  ]   |
|  Duration        [ 389 ms                                   ]   |
|                                                                 |
|  Stdout                                                         |
|  ┌───────────────────────────────────────────────────────────┐  |
|  │ (empty)                                                   │  |
|  └───────────────────────────────────────────────────────────┘  |
|  Stderr                                                         |
|  ┌───────────────────────────────────────────────────────────┐  |
|  │ (empty)                                                   │  |
|  └───────────────────────────────────────────────────────────┘  |
+-----------------------------------------------------------------+
```

---

## VM Image

A kernel + rootfs pair.

| Field              | Type   | Reqd | Notes                                       |
| ------------------ | ------ | ---- | ------------------------------------------- |
| `image_name`       | Data   | Y    | Primary key. e.g. `ubuntu-24.04`.           |
| `description`      | Data   |      | Free text.                                  |
| `kernel_url`       | Data   | Y    | HTTPS URL of the uncompressed vmlinux.      |
| `kernel_filename`  | Data   | Y    | Filename to store as. e.g. `vmlinux-6.1.141`.|
| `kernel_sha256`    | Data   | Y    | Hex digest. Verified on download.           |
| `rootfs_url`       | Data   | Y    | HTTPS URL of the squashfs (we convert to ext4).|
| `rootfs_filename`  | Data   | Y    | Final ext4 name, e.g. `ubuntu-24.04.ext4`.   |
| `rootfs_sha256`    | Data   | Y    | Hex digest of the source squashfs.           |
| `default_disk_gb`  | Int    | Y    | Defaults to 4. Each VM gets a copy resized to this. |
| `is_active`        | Check  |      | Defaults to 1.                              |

Buttons:

- **Sync to All Nodes** — for every `Active` `Metal Node`, ensure the image is
  present in `/var/lib/atlas/images/<image_name>/`. Idempotent.
- **Sync to Node** — same, for a single node (used from the Metal Node form).

See [08-images.md](./08-images.md) for the layout and per-VM copy strategy.

### Desk wireframe — VM Image form

```
+-----------------------------------------------------------------+
| VM Image: ubuntu-24.04                          [Active]    [v] |
+-----------------------------------------------------------------+
|  Image Name *        [ ubuntu-24.04                          ]  |
|  Description         [ Firecracker CI Ubuntu 24.04 rootfs    ]  |
|                                                                 |
|  Kernel                                                         |
|  Kernel URL *        [ https://s3.amazonaws.com/.../vmlinux- ]  |
|  Kernel Filename *   [ vmlinux-6.1.141                       ]  |
|  Kernel SHA-256 *    [ a3f9...                               ]  |
|                                                                 |
|  Rootfs                                                         |
|  Rootfs URL *        [ https://s3.amazonaws.com/.../ubuntu-2 ]  |
|  Rootfs Filename *   [ ubuntu-24.04.ext4                     ]  |
|  Rootfs SHA-256 *    [ 7b21...                               ]  |
|  Default Disk (GB) * [ 4                                     ]  |
|                                                                 |
|  [x] Is Active                                                  |
|                                                                 |
|  [ Sync to All Nodes ]                                          |
+-----------------------------------------------------------------+
```
