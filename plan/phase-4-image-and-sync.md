# Phase 4 — Virtual Machine Image + sync-image

> **Historical.** The e2e tests that lived here now live in
> [`use_cases/image_sync.py`](../atlas/tests/e2e/use_cases/image_sync.py).
> Going-forward testing guideline: [e2e-testing.md](./e2e-testing.md).

## Goal

A `Virtual Machine Image` DocType and a working **Sync to Server** /
**Sync to All Servers** action that runs
[`../scripts/sync-image.sh`](../scripts/sync-image.sh) as an enqueued Task per
(image, server) pair.

## You can do this at the end

1. Create a `Virtual Machine Image` row pointing at the public
   Firecracker CI Ubuntu 24.04 artifacts.
2. Click **Sync to All Servers** → one background job per Active server.
3. Each job creates a Task; on success, the server has
   `/var/lib/atlas/images/ubuntu-24.04/vmlinux-...` and
   `/var/lib/atlas/images/ubuntu-24.04/ubuntu-24.04.ext4` with the right
   SHA-256 (for the kernel) and the right ext4 size.

## Files added or changed

### DocType

- `atlas/atlas/atlas/doctype/virtual_machine_image/` — full set.

### Scripts (existing files edited)

Per Taste rule "every shell script in `scripts/` must be idempotent" (see
[`../llm/Taste.md`](../llm/Taste.md)): re-running `sync-image.sh` on a server
that already has the image must short-circuit on the existing checksums and
exit clean. Retry == re-run, never a special repair mode.

- [`../scripts/sync-image.sh`](../scripts/sync-image.sh) — `sudo` prefix per
  convention.

### Tests

- `atlas/atlas/atlas/doctype/virtual_machine_image/test_virtual_machine_image.py`
- `atlas/atlas/tests/e2e/phase_4.py`

## DocType

Schema per [`../spec/02-doctypes.md#virtual-machine-image`](../spec/02-doctypes.md#virtual-machine-image).

- `image_name`: primary key (`autoname = "field:image_name"`).
- `is_active`: Check, default 1.
- Buttons: **Sync to All Servers**, **Sync to Server**.

### `sync_to_all_servers(self)` (whitelisted)

```python
def sync_to_all_servers(self) -> list[str]:
    """For each Active server, enqueue one sync Task. Returns Task names."""
```

Steps:

1. Query `frappe.get_all("Server", filters={"status": "Active"}, pluck="name")`.
2. For each, call `self.sync_to_server(server_name)` which enqueues.
3. Return the list of Task names.

### `sync_to_server(self, server_name)` (whitelisted)

```python
def sync_to_server(self, server_name: str) -> str:
    """Enqueue a sync Task. Returns Task name."""
```

Steps:

1. Insert a `Task` row with `status="Pending"`, `server=server_name`,
   `script="sync-image.sh"`, and the variables block (see below).
2. `frappe.enqueue("atlas.atlas.atlas.ssh.execute_task", task_name=task.name,
   queue="long", timeout=1800)`.
3. Return `task.name`.

`execute_task` is the function landed in phase 1. We extend it here only if
we discover it needs server-side context — we expect we don't, because the
Task row carries the server reference.

But `sync-image.sh` requires a side-channel **file** (`GUEST_NETWORK_UNIT`)
on the server: the
[`../scripts/guest/atlas-network.service`](../scripts/guest/atlas-network.service)
file must be uploaded *before* the script runs. So we need a small
pre-run hook.

### The "pre-run hook" pattern

We don't want every Task type to grow a Python "do this first" function. The
clean rule: **scripts that need extra files on the server have their file
list embedded in a sibling Python helper, called by `execute_task` based on
the script name.**

Concrete:

```python
# atlas/atlas/atlas/script_uploads.py
SCRIPT_UPLOADS: dict[str, list[tuple[str, str]]] = {
    "sync-image.sh": [
        ("scripts/guest/atlas-network.service",
         "/tmp/atlas/atlas-network.service"),
    ],
    # bootstrap-server.sh's uploads stay where they are — they're durable
    # state (helpers + unit), uploaded by Server.bootstrap() not by execute_task.
}

def files_to_upload(script: str) -> list[tuple[str, str]]:
    return SCRIPT_UPLOADS.get(script, [])
```

`execute_task` and `run_task` both consult `files_to_upload(script)` before
running, and `upload_files` puts the files in place. Sync's
`GUEST_NETWORK_UNIT` env var points at `/tmp/atlas/atlas-network.service`.

This is the **only** divergence from "scripts are self-contained" that the
spec admits in
[`../spec/08-images.md`](../spec/08-images.md#sync-to-a-server). It is
intentional — the guest unit file is content, not code.

### Variables block

```python
{
    "IMAGE_NAME": image.image_name,
    "KERNEL_URL": image.kernel_url,
    "KERNEL_FILENAME": image.kernel_filename,
    "KERNEL_SHA256": image.kernel_sha256,
    "ROOTFS_URL": image.rootfs_url,
    "ROOTFS_FILENAME": image.rootfs_filename,
    "ROOTFS_SHA256": image.rootfs_sha256,
    "DEFAULT_DISK_GB": str(image.default_disk_gigabytes),
    "GUEST_NETWORK_UNIT": "/tmp/atlas/atlas-network.service",
}
```

No secrets in this block. Image URLs are public.

### Timeout

`sync-image.sh` downloads ~600MB of rootfs squashfs and unsquashes it. On a
slow link this can be 10+ minutes. The phase-1 default of 1800s is fine.
Pin `frappe.enqueue(timeout=1800)`.

### Concurrency

If two sync Tasks for the same image on the same server start at once they'd
both write `${rootfs_filename}.part`. We **do not** guard against this in
this phase. Operator discipline: don't double-click. If it becomes a problem
we add a Server-level lock later (a row in a "Server Lock" doctype, set by
`execute_task`). Flagged.

## Test plan

### Unit tests

- `test_virtual_machine_image_validate_urls_https`: assert validation rejects
  `http://` and `ftp://` URLs.
- `test_sync_to_server_enqueues_task`: mock `frappe.enqueue`, assert one
  call with the right args, assert a Task row exists with `Pending` status.
- `test_sync_to_all_servers_enqueues_one_per_active_server`: insert 3
  servers (1 Active, 1 Broken, 1 Archived); assert 1 enqueued.
- `test_files_to_upload_for_sync_image`: assert
  `files_to_upload("sync-image.sh")` includes the guest unit.

### E2E (`tests/e2e/phase_4.py`)

Builds on phase 3's bootstrapped server.

1. Pre-sweep. Provision a fresh droplet (or reuse phase 3's surviving one if
   `--reuse-server` flag is set — convenience for dev iteration).
2. Insert a `Virtual Machine Image` row with the Firecracker CI URLs +
   SHA-256s. (Constants live in `tests/e2e/_shared.py`.)
3. Call `image.sync_to_server("server-e2e")`.
4. Poll the enqueued Task until `Success` or `Failure`, timeout 15 minutes.
5. Assert:
   - Task `Success`, exit_code 0.
   - `/var/lib/atlas/images/ubuntu-24.04/vmlinux-...` exists on the server
     (verified via a small probe `run_task`).
   - `/var/lib/atlas/images/ubuntu-24.04/ubuntu-24.04.ext4` exists and is
     at least `DEFAULT_DISK_GB * 1024MB - 5%` in size.
   - The ext4 contains the guest unit:
     `debugfs -R 'stat /etc/systemd/system/atlas-network.service' <path>`
     in a probe script returns 0 (file present). Alternative: just check the
     symlink: `debugfs -R 'stat /etc/systemd/system/multi-user.target.wants/atlas-network.service'`.
6. **Re-sync** the same image (idempotency): assert Task Success and that
   it short-circuits (stdout contains "Kernel already present" and "Rootfs
   already built").
7. `finally`: delete the droplet (unless `--keep-server` for dev).

## What we are NOT doing in this phase

- No image-build pipeline (no Image Build doctype).
- No image GC / cleanup on the server. `delete-vm.sh` doesn't touch images.
- No checksum verification of the **derived** ext4 (we trust mkfs.ext4
  reproducibility-ish; the source squashfs is checksummed).
- No multi-arch. `ARCHITECTURE = x86_64` hard-coded in the script.
- No multiple images. We test only with `ubuntu-24.04`.
- No locking. Operator avoids double-clicks.

## Spec drift introduced

See [drift.md](./drift.md#phase-4):

- The spec says `GUEST_NETWORK_UNIT` is uploaded by "the caller before
  running this script." We formalize the caller as
  `script_uploads.py::SCRIPT_UPLOADS` + `execute_task`. The contract is
  unchanged; the implementation location is named.
- The script uses `mkfs.ext4 -d` to build the ext4 from the unsquashed
  directory in one shot, then `mv`s into place. The spec describes the steps
  in the same order but doesn't enumerate the staging directory. No
  divergence; just documenting the implementation detail.
