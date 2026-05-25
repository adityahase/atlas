# Phase 6 — VM lifecycle: Start, Stop, Restart, Delete

## Goal

Wire the remaining four buttons on Virtual Machine. The shell scripts already
exist; this phase is the Python state machine.

## You can do this at the end

For a Running VM:

- **Stop** → status `Stopped`, `last_stopped` set, Firecracker process gone.
- **Start** → status `Running`, `last_started` set, VM reachable.
- **Restart** → effectively Stop then Start.
- **Delete** → status `Archived`, on-disk state under
  `/var/lib/atlas/virtual-machines/<uuid>/` gone, systemd unit disabled.
  The Virtual Machine row stays in the database. The UUID does not change.

## Files added or changed

### DocType

- `atlas/atlas/atlas/doctype/virtual_machine/virtual_machine.py` — add
  `start()`, `stop()`, `restart()`, `delete_vm()` methods. `delete_vm` (not
  `delete`) to avoid colliding with Frappe's Document.delete().
- `atlas/atlas/atlas/doctype/virtual_machine/virtual_machine.js` — wire the
  four buttons to call those server methods, with state-machine guards
  (Start enabled only when Stopped, etc.).

### Scripts (existing files edited)

- [`../scripts/start-vm.sh`](../scripts/start-vm.sh),
  [`stop-vm.sh`](../scripts/stop-vm.sh),
  [`delete-vm.sh`](../scripts/delete-vm.sh) — `sudo` prefix.

### Tests

- `atlas/atlas/atlas/doctype/virtual_machine/test_virtual_machine_lifecycle.py`
- `atlas/atlas/tests/e2e/phase_6.py`

## State machine

| Method   | Legal from               | Final status | Side effect                                |
|----------|--------------------------|--------------|--------------------------------------------|
| start    | Stopped                  | Running      | `last_started = now()`                     |
| stop     | Running                  | Stopped      | `last_stopped = now()`                     |
| restart  | Running, Stopped         | Running      | `last_stopped` and `last_started` updated  |
| delete_vm| Pending, Provisioning, Running, Stopped, Failed | Archived | (Archived is terminal) |

Provision (phase 5) and these four are the only state transitions. Anything
else raises `frappe.ValidationError("Cannot {action} from {status}")`.

Restart is `stop()` then `start()` in Python — two Tasks. The spec is
explicit about this choice (no `restart-vm.sh`).

## Methods

```python
def start(self) -> str:
    if self.status != "Stopped":
        frappe.throw(f"Cannot start a {self.status} VM")
    task = run_task_on_server(
        server=self.server,
        script="start-vm.sh",
        variables={"VIRTUAL_MACHINE_NAME": self.name},
        virtual_machine=self.name,
        timeout_seconds=30,
    )
    self.status = "Running"
    self.last_started = frappe.utils.now_datetime()
    self.save()
    return task.name

def stop(self) -> str:
    if self.status != "Running":
        frappe.throw(f"Cannot stop a {self.status} VM")
    task = run_task_on_server(
        server=self.server,
        script="stop-vm.sh",
        variables={"VIRTUAL_MACHINE_NAME": self.name},
        virtual_machine=self.name,
        timeout_seconds=30,
    )
    self.status = "Stopped"
    self.last_stopped = frappe.utils.now_datetime()
    self.save()
    return task.name

def restart(self) -> tuple[str, str]:
    """Returns (stop_task_name, start_task_name)."""
    stop_task = self.stop() if self.status == "Running" else None
    start_task = self.start()
    return (stop_task, start_task)

def delete_vm(self) -> str:
    if self.status == "Archived":
        frappe.throw("VM is already archived")
    task = run_task_on_server(
        server=self.server,
        script="delete-vm.sh",
        variables={"VIRTUAL_MACHINE_NAME": self.name},
        virtual_machine=self.name,
        timeout_seconds=60,
    )
    self.status = "Archived"
    self.save()
    return task.name
```

### Why catch exceptions broadly?

We don't — `run_task_on_server` raises `frappe.ValidationError` on Task
failure and that propagates to Desk where the operator sees the error. The
VM status only updates **after** Task success, so a failed Stop leaves the
VM `Running`. That's correct.

The one exception is `delete_vm`: even if `delete-vm.sh` partially fails,
we still want to archive the row (operator can clean up on the server
manually). So `delete_vm`:

```python
try:
    task = run_task_on_server(...)
except frappe.ValidationError:
    # Re-raise so the operator sees it, but DO archive the row first.
    self.status = "Archived"
    self.save()
    raise
else:
    self.status = "Archived"
    self.save()
    return task.name
```

Actually no — if we archive on failure, the row says "Archived" but the
on-host state is dirty. Operator loses track. Better: **don't auto-archive
on failure**; let the operator click Delete again, which is safe because
`delete-vm.sh` is idempotent. The script handles missing unit, missing
directory, missing socket — all fine.

Final rule: status moves to `Archived` only on successful delete Task. On
failure, status stays put and the operator retries. Simple.

### JS button state machine

In the form's `.js`, hide/show buttons based on `frm.doc.status`:

```javascript
frappe.ui.form.on('Virtual Machine', {
    refresh(frm) {
        const status = frm.doc.status;
        const allowed = {
            Pending:      ['provision', 'delete_vm'],
            Provisioning: [],
            Running:      ['stop', 'restart', 'delete_vm'],
            Stopped:      ['start', 'restart', 'delete_vm'],
            Failed:       ['provision', 'delete_vm'],
            Archived:     [],
        }[status] ?? [];
        for (const action of ['provision', 'start', 'stop', 'restart', 'delete_vm']) {
            // remove the existing button and re-add only if allowed.
        }
        if (allowed.includes('start')) {
            frm.add_custom_button('Start', () => frm.call('start').then(() => frm.reload_doc()));
        }
        // ... etc
    }
});
```

The server-side guards are authoritative; the JS just keeps the form
honest.

## Test plan

### Unit tests

For each method: legal-from-state succeeds, illegal-from-state raises.
Mock `run_task_on_server`.

- `test_start_from_stopped_succeeds`.
- `test_start_from_running_raises`.
- `test_stop_from_running_succeeds`.
- `test_stop_from_stopped_raises`.
- `test_restart_calls_stop_then_start`.
- `test_restart_from_stopped_only_calls_start`.
- `test_delete_vm_succeeds_from_running_archives_row`.
- `test_delete_vm_failure_does_not_archive`.
- `test_delete_vm_idempotent_when_already_archived`: assert raise (already
  archived).
- `test_status_change_updates_last_started_and_last_stopped`.

### E2E (`tests/e2e/phase_6.py`)

Builds on phase 5. Reuses or provisions a Running VM.

1. Pre-sweep.
2. Provision a VM (using phase 5's flow). Status=Running.
3. Assert `systemctl is-active firecracker-vm@<uuid>` is 0 on the server.
4. Stop. Assert status=Stopped, `last_stopped` set. Assert `is-active`
   returns non-zero on the server.
5. Start. Assert status=Running, `last_started` advanced. Assert
   `is-active` 0.
6. Restart. Assert two Tasks created. Assert `is-active` 0.
7. Delete. Assert status=Archived. Assert
   `[ -d /var/lib/atlas/virtual-machines/<uuid> ]` returns non-zero (gone)
   on the server. Assert the unit file (`firecracker-vm@<uuid>.service`)
   is disabled.
8. Delete again. Assert raise (already archived).
9. Inspect the Server: `ip link show <tap_device>` returns "Device not
   found" — networking cleaned up.
10. `finally`: nothing extra (VM already archived; server stays).

## What we are NOT doing in this phase

- No bulk operations ("Stop all VMs on Server X"). One VM at a time.
- No `force_delete` / kill -9. `delete-vm.sh` uses `systemctl disable
  --now` and `rm -rf`; if the unit is stuck, operator intervenes manually.
- No reconciliation. If a VM dies and systemd restarts it, status stays
  Running; if it dies and stays dead, status stays Running but the VM is
  gone. Phase-8-deferred work.
- No "Recreate" button. Archive + create new VM is the path.
- No backup before delete. Once archived, the on-host bytes are gone.
- No editing immutable fields. Operator archives + recreates.

## Spec drift introduced

See [drift.md](./drift.md#phase-6):

- Spec says "delete-vm.sh sets `status = Archived`." We move that
  responsibility to Python (after Task success). Cleaner: scripts produce
  side effects on the host; status changes happen in the controller. The
  spec wording is OK either way; we make it explicit.
- The method is `delete_vm` not `delete` (Frappe's Document.delete is taken).
  Button label says "Delete." Trivial naming choice.
