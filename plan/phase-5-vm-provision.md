# Phase 5 — Virtual Machine DocType (Provision only)

> **Historical.** The e2e tests that lived here now live in
> [`use_cases/virtual_machine_provisioning.py`](../atlas/tests/e2e/use_cases/virtual_machine_provisioning.py).
> Going-forward testing guideline: [e2e-testing.md](./e2e-testing.md).

## Goal

Add the `Virtual Machine` DocType with autoname UUID, derived MAC and TAP
fields, an IPv6 allocator, and the **Provision** button. By the end of this
phase, the operator can create a VM through Desk and SSH into it over IPv6.

Start/Stop/Delete come in phase 6.

## You can do this at the end

1. From Desk: Atlas → Virtual Machine → New. Pick `server-blr1-test`, image
   `ubuntu-24.04`, vCPUs=1, memory=512, disk=4, paste an SSH public key,
   write a description. Save.
2. Click **Provision**. Watch the Task row appear and reach `Success` in
   ~3 seconds (image is already on the server from phase 4).
3. From a host with IPv6 connectivity:
   `ssh root@2a03:b0c0:abcd:1234::2`. You're in. `ip -6 addr` shows the
   right address. `systemctl status atlas-network` is `active`.

## Files added or changed

### DocType

- `atlas/atlas/atlas/doctype/virtual_machine/` — full set.

### Scripts (existing files edited)

Per Taste rule "every shell script in `scripts/` must be idempotent" (see
[`../llm/Taste.md`](../llm/Taste.md)): re-running `provision-vm.sh` for the
same VM name must converge to the same on-disk layout. The operator clicks
Provision again on failure; there is no repair script.

- [`../scripts/provision-vm.sh`](../scripts/provision-vm.sh) — `sudo` prefix.

### Module additions

- `atlas/atlas/atlas/networking.py` — IPv6 allocator + MAC + tap helpers.

### Tests

- `atlas/atlas/atlas/doctype/virtual_machine/test_virtual_machine.py`
- `atlas/atlas/tests/test_networking.py`
- `atlas/atlas/tests/e2e/phase_5.py`

## DocType

Schema per [`../spec/02-doctypes.md#virtual-machine`](../spec/02-doctypes.md#virtual-machine).

- `name`: UUID via `autoname = "hash"` — but spec says UUID, not hash.
  Override: `before_insert` sets `self.name = str(uuid.uuid4())`. The
  `hash`-named field on Task is fine for short audit rows; VM names need
  UUIDs because they show up on disk paths.
- `server`: Link → Server, immutable after first provision. Enforce in
  `validate()`: `if not self.is_new() and self.has_value_changed("server"):
   raise`.
- Same for `image`, `vcpus`, `memory_megabytes`, `disk_gigabytes`. Spec lists
  them as immutable.
- `ipv6_address`, `mac_address`, `tap_device`: set by
  `before_insert` (allocator/derivation). Hidden in the form, shown
  read-only in the wireframe.
- `status` options: `Pending`, `Provisioning`, `Running`, `Stopped`, `Failed`,
  `Archived`.
- Buttons: **Provision** (this phase). Other buttons appear in the form's
  `.js` but are stubbed; phase 6 wires them.

### `before_insert(self)`

```python
def before_insert(self):
    self.name = str(uuid.uuid4())
    self.mac_address = derive_mac(self.name)
    self.tap_device = derive_tap(self.name)
    self.ipv6_address = allocate_ipv6(self.server)
    self.status = "Pending"
```

### `provision(self)` (whitelisted)

```python
def provision(self) -> str:
    """Run provision-vm.sh as one Task. Requires the image to be present
    on the server already; fails fast otherwise. Returns Task name."""
```

Steps:

1. Validate `self.status in ("Pending", "Failed")`.
2. **Require image already on server.** Probe with a small `run_task` that
   runs `[ -f /var/lib/atlas/images/<image>/<rootfs_filename> ]`.
   - If absent: raise
     `frappe.ValidationError("Image '<image>' is not present on server
     '<server>'. Sync the image first (Virtual Machine Image → Sync to
     Server) and retry.")`. The VM stays in its current status (`Pending`
     or `Failed`); no Task is created.
   - If present: continue.
3. Set `status = "Provisioning"`, save.
4. Call:
   ```python
   task = run_task_on_server(
       server=self.server,
       script="provision-vm.sh",
       variables=self._provision_variables(),
       virtual_machine=self.name,
       timeout_seconds=120,
   )
   ```
5. On Task `Success`: `status = "Running"`, `last_started = now()`, save.
   On exception: `status = "Failed"`, save, re-raise.
6. Return `task.name`.

### Rationale

The operator's mental model is: "syncing an image is a deliberate,
multi-minute action; provisioning a VM is fast." Hiding a multi-minute sync
behind the Provision button violates that. Requiring an explicit sync first
makes the slow path slow and visible, and the fast path fast.

The image-presence probe is one extra SSH round-trip (~100ms). Acceptable.

### `_provision_variables(self)`

```python
{
    "VIRTUAL_MACHINE_NAME": self.name,
    "IMAGE_NAME": self.image,
    "KERNEL_FILENAME": frappe.db.get_value("Virtual Machine Image", self.image, "kernel_filename"),
    "ROOTFS_FILENAME": frappe.db.get_value("Virtual Machine Image", self.image, "rootfs_filename"),
    "VCPUS": str(self.vcpus),
    "MEMORY_MB": str(self.memory_megabytes),
    "DISK_GB": str(self.disk_gigabytes),
    "MAC_ADDRESS": self.mac_address,
    "TAP_DEVICE": self.tap_device,
    "VIRTUAL_MACHINE_IPV6": self.ipv6_address,
    "SSH_PUBLIC_KEY": self.ssh_public_key,
}
```

`SSH_PUBLIC_KEY` is a Long Text field on the VM, written by the operator. We
do not store private keys on the VM. The operator manages the keypair on the
client side.

## `atlas/atlas/atlas/networking.py`

```python
def derive_mac(virtual_machine_name: str) -> str:
    """06:00: + first 4 bytes of UUID, hex-colons.
    Example: '06:00:d4:f7:c1:a2'."""

def derive_tap(virtual_machine_name: str) -> str:
    """atlas- + first 10 hex chars (UUID without dashes).
    Example: 'atlas-d4f7c1a27e'. Length 16, the IFNAMSIZ limit."""

def allocate_ipv6(server_name: str) -> str:
    """Atomically select the next free address in the server's /124.
    SELECT FOR UPDATE the Server row, scan existing VM addresses (including
    Archived), pick the lowest unused. Reserve ::0 for the network and ::1
    for the host. Raise frappe.ValidationError('No IPv6 capacity') when full."""

def carve_virtual_machine_range(prefix_cidr: str) -> str:
    """Already exists from phase 3."""
```

`allocate_ipv6` implementation outline:

```python
def allocate_ipv6(server_name: str) -> str:
    server = frappe.get_doc("Server", server_name, for_update=True)
    network = ipaddress.IPv6Network(server.ipv6_virtual_machine_range)
    used = set(
        frappe.get_all(
            "Virtual Machine",
            filters={"server": server_name},
            pluck="ipv6_address",
        )
    )
    for index, candidate in enumerate(network.hosts()):
        if index < 2:                        # ::0 and ::1 reserved
            continue
        if str(candidate) not in used:
            return str(candidate)
    raise frappe.ValidationError("No IPv6 capacity on server")
```

Note: `ipaddress.IPv6Network.hosts()` excludes the subnet-router anycast
address (`::0`) automatically, so the `index < 2` actually only excludes
`::1`. Adjust precisely in code — test pins exact behavior.

## Test plan

### Unit tests (networking)

- `test_derive_mac_stable`: same UUID → same MAC.
- `test_derive_tap_length_16`: random UUIDs all produce 16-char names.
- `test_allocate_ipv6_picks_first_free_starting_at_2`: empty server returns
  `::2`.
- `test_allocate_ipv6_skips_used`: with `::2`, `::3` used → returns `::4`.
- `test_allocate_ipv6_reuses_terminated_addresses`: Terminated VMs release
  their address back into the pool; the next allocation reclaims it.
- `test_allocate_ipv6_raises_when_full`: fill the /124, assert raise.
- `test_carve_virtual_machine_range`: a couple of /64 inputs.

### Unit tests (Virtual Machine)

- `test_before_insert_sets_uuid_mac_tap_ipv6`: insert a VM, assert all four.
- `test_immutable_fields_raise`: insert, modify `vcpus`, assert raise on
  save.
- `test_provision_runs_when_image_present`: mock the probe to return
  "exists", mock `run_task_on_server`, assert provision-vm Task ran and
  status flipped to Running.
- `test_provision_raises_when_image_absent`: mock probe → absent. Assert
  raise with the expected message. Assert no provision-vm Task was
  created. Assert VM status unchanged from `Pending`.
- `test_provision_failure_marks_failed`: image present, mock provision-vm
  task to raise, assert VM status=Failed.

### E2E (`tests/e2e/phase_5.py`)

Builds on phase 4 (bootstrapped server with image present).

1. Pre-sweep. Provision or reuse a server.
2. Sync the Ubuntu image to it.
3. Generate an ephemeral keypair in `/tmp/atlas-e2e/`, mode 0600.
4. Insert a VM doc with `ssh_public_key` = the pub key, description
   "phase 5".
5. **Negative check**: temporarily rename the image on the server
   (`mv .../ubuntu-24.04.ext4 .ext4.bak` via a probe Task), call
   `vm.provision()`, assert `ValidationError` with "not present" in
   the message, assert no provision Task created. Rename back.
6. Call `vm.provision()` (image now present).
7. Assert: Task Success in <10s. VM status=Running.
8. Wait ~5s for the guest to boot.
9. From the bench host (assumed IPv6 reachable; flagged if not), SSH:
   `ssh -i /tmp/atlas-e2e/id -o StrictHostKeyChecking=no
        root@<vm.ipv6_address> hostname`.
   Assert returncode 0, stdout is non-empty.
10. **Re-provision** (idempotency on the script): call `vm.provision()` again.
   Spec status validation will raise because status=Running. Expected. So
   instead: manually flip status back to Pending in the test and re-run.
   Assert Task Success. The script handles "rootfs already exists" by
   skipping the copy.
11. `finally`: delete the VM directory on the server via a probe
    `rm -rf` (phase 6 will give us a real delete button); leave the server.

### IPv6 reachability concern in e2e

If the test bench has no IPv6 to the public internet, step 9 will fail. The
e2e detects this with a `getent ahosts ipv6.google.com` precheck and skips
step 9 with a printed warning. We still assert that the VM **booted** via
checking `systemctl is-active firecracker-vm@<uuid>` on the server.

## What we are NOT doing in this phase

- No Start/Stop/Delete buttons (phase 6).
- No "Recent Tasks" or "Virtual Machines on this server" child tables on the
  Server form (phase 7 polish).
- No description-based search, no list-view tweaks for the VM UUID. The list
  shows UUIDs and descriptions; that's the spec.
- No quota check ("operator can create unlimited VMs" — fine for iteration).
- No live boot detection. We assert systemd `is-active`; we do **not** wait
  for SSH-in-guest to succeed before reporting Provision done. The 5-second
  sleep in the e2e is for testing only.
- No retry on transient SSH failure to the bench host.

## Spec drift introduced

See [drift.md](./drift.md#phase-5):

- Spec says `Virtual Machine.name` is set by "autoname on insert" — we
  interpret this as `before_insert` with `uuid.uuid4()` because Frappe's
  built-in `autoname=hash` is a 10-char hash, not a UUID. The Task DocType
  keeps `hash` since 10 chars is enough for audit rows.
- Spec's allocation order: spec implies the allocator iterates `::2, ::3,
  ::4, ...`. We honor "skip the host (::1) and the subnet ID (::0)" exactly.
  Test pins this.
- Spec says `last_started = now()` only on Start (`05-virtual-machine-lifecycle.md`).
  Our provision flow also sets it because Provision ends with the VM running.
  Open question logged in drift.md; we go with "set on every Running
  transition" as the obvious answer.
