# Phase 3 — Server Provider + Server DocTypes (with bootstrap)

## Goal

Two DocTypes (`Server Provider`, `Server`) and a working **Provision Server**
button that, given an active provider, creates a DO droplet, inserts a Server,
and bootstraps it. The bootstrap is one enqueued Task running
[`../scripts/bootstrap-server.sh`](../scripts/bootstrap-server.sh).

## You can do this at the end

1. Create a `Server Provider` row with a real DO token + SSH key.
2. Click **Test Connection** → "OK, account valid."
3. Click **Provision Server**, enter `server-blr1-test` → background job
   starts, droplet appears in DO, Server row is created, bootstrap Task
   appears as `Running`.
4. Watch the Task row flip to `Success` after ~45 seconds.
5. SSH into the server manually: `firecracker --version` prints `1.15.1`,
   `/var/lib/atlas/` exists with `images/`, `virtual-machines/`, `run/`,
   `bin/` subdirectories. `vm-network-up.sh` and `vm-network-down.sh` are in
   `bin/`. `/etc/systemd/system/firecracker-vm@.service` is in place.

## Files added or changed

### DocTypes

- `atlas/atlas/atlas/doctype/server_provider/` — full set (json/py/js/test).
- `atlas/atlas/atlas/doctype/server/` — full set.

### Scripts (existing files edited)

- [`../scripts/bootstrap-server.sh`](../scripts/bootstrap-server.sh) — prefix
  privileged commands with `sudo` per the convention in
  [`00-overview.md`](./00-overview.md#shell-scripts-call-sudo).
- [`../scripts/vm-network-up.sh`](../scripts/vm-network-up.sh),
  [`vm-network-down.sh`](../scripts/vm-network-down.sh) — same `sudo` prefix.

### Module additions

- `atlas/atlas/atlas/server_provider.py` — provisioning logic outside the
  controller (so the controller stays thin and the function is easy to call
  from tests and the e2e). Wraps the DO client + Server insert + enqueue.
- `atlas/atlas/atlas/ssh.py` — add `run_task_on_server(server, ...)` thin
  wrapper that loads connection info from a Server doc and calls `run_task`.
- `atlas/atlas/atlas/ssh.py` — add `connection_for_server(server) -> dict` so
  `Server.bootstrap()` can also call `upload_files`.

### Tests

- `atlas/atlas/atlas/doctype/server_provider/test_server_provider.py`
- `atlas/atlas/atlas/doctype/server/test_server.py`
- `atlas/atlas/tests/e2e/phase_3.py`

## Server Provider DocType

Schema per [`../spec/02-doctypes.md#server-provider`](../spec/02-doctypes.md#server-provider).
Implementation specifics:

- `provider_name`: Data, primary key (`autoname = "field:provider_name"`).
- `provider_type`: Select, options just `DigitalOcean` for now.
- `api_token`, `ssh_private_key`: Password fields. Read via `get_secret()`.
- `is_active`: Check, default 1.
- Buttons (in `.js`): **Test Connection**, **Provision Server**.

### `Test Connection`

Server-side method `test_connection(self)`. Whitelisted, type-annotated.
Calls `DigitalOceanClient(self.api_token).account()`. Returns `{"ok": True}`
or raises with the DO error body.

### `Provision Server`

JS opens a dialog asking for `server_name`. Calls server method
`provision_server(self, server_name: str)`:

```python
def provision_server(self, server_name: str) -> str:
    """Create a droplet, insert a Server row, enqueue bootstrap.
    Returns the Server name (== server_name)."""
```

Inside:

1. Validate `server_name` is unique (`frappe.db.exists("Server", server_name)`).
2. `DigitalOceanClient(token).create_droplet(name=server_name,
   region=self.default_region, size=self.default_size,
   image=self.default_image, ssh_key_ids=[self.ssh_key_id],
   tags=["atlas"], ipv6=True)`.
3. Insert a `Server` row immediately with `status = "Pending"` and
   `provider_resource_id = droplet["id"]`. Populate `region`, `size` from the
   form defaults.
4. `frappe.enqueue("atlas.atlas.server_provider.finish_provisioning",
                   server_name=server_name, droplet_id=droplet["id"], queue="long")`.
5. Return `server_name`.

`finish_provisioning(server_name, droplet_id)` (module-level function so
`frappe.enqueue` can resolve it):

1. `wait_for_active(droplet_id)`.
2. Set `Server.ipv4_address`, `ipv6_address`, `ipv6_prefix`,
   `ipv6_virtual_machine_range` (carve /124 from the /64).
3. Set `Server.status = "Bootstrapping"`.
4. Call `Server.bootstrap()` (sync, inside the worker — no nested enqueue).
5. On success: `status = "Active"`. On failure: `status = "Broken"`.

### `/124` carve-out

The first /124 of the /64. If DO gives `2a03:b0c0:abcd:1234::/64`, the /124 is
`2a03:b0c0:abcd:1234::/124` (covers `::0` through `::F`). Helper in
`atlas/atlas/atlas/networking.py` (introduced in phase 5, but the function
lands here):

```python
def carve_virtual_machine_range(prefix_cidr: str) -> str:
    """Given the /64 DO gave us, return the /124 we hand addresses out from."""
```

Spec drift note: the spec assumes this is always "the first /124." We bake
that in. Flagged in [drift.md](./drift.md#phase-3).

## Server DocType

Schema per [`../spec/02-doctypes.md#server`](../spec/02-doctypes.md#server).

- `server_name`: primary key (`autoname = "field:server_name"`).
- `status` options: `Pending`, `Bootstrapping`, `Active`, `Draining`,
  `Broken`, `Archived`.
- Buttons: **Bootstrap**, **Run Task**, **Reboot**. **Run Task** is a stub in
  this phase that just opens a "TODO phase 7" dialog; **Reboot** is also
  stubbed (`systemctl reboot` is dangerous and tested in phase 7's polish).

### `Server.bootstrap()` (controller method, whitelisted)

```python
def bootstrap(self) -> str:
    """Upload helper scripts + unit file, then run bootstrap-server.sh as
    one Task. Idempotent. Returns the Task name."""
```

Steps:

1. Verify status is in `("Pending", "Bootstrapping", "Active", "Broken")`.
   (Re-bootstrap is the recovery path.)
2. Build a connection dict: `host=self.ipv4_address`,
   `ssh_private_key=get_secret("Server Provider", self.provider, "ssh_private_key")`,
   `user="root"`.
3. `upload_files(connection, [
       ("scripts/vm-network-up.sh", "/var/lib/atlas/bin/vm-network-up.sh"),
       ("scripts/vm-network-down.sh", "/var/lib/atlas/bin/vm-network-down.sh"),
       ("scripts/systemd/firecracker-vm@.service",
        "/etc/systemd/system/firecracker-vm@.service"),
   ])`
   — note `/var/lib/atlas/bin` may not exist yet on a fresh droplet, so the
   uploader `mkdir -p`s parent dirs first. Add to `ssh.upload_files`.
4. Run the Task:
   ```
   task = run_task_on_server(
       server=self.name,
       script="bootstrap-server.sh",
       variables={
           "FIRECRACKER_VERSION": "v1.15.1",
           "ARCHITECTURE": "x86_64",
       },
   )
   ```
5. Parse the trailing `KEY=value` lines from `task.stdout` into `architecture`,
   `firecracker_version`, `kernel_version`. Save.
6. Return `task.name`.

### `run_task_on_server(server, script, variables, **kwargs)`

```python
def run_task_on_server(
    server: str,
    script: str,
    variables: dict,
    virtual_machine: str | None = None,
    timeout_seconds: int = 1800,
) -> "Task":
    server_doc = frappe.get_doc("Server", server)
    connection = connection_for_server(server_doc)
    return run_task(connection, script, variables,
                    server=server, virtual_machine=virtual_machine,
                    timeout_seconds=timeout_seconds)
```

`connection_for_server` reads the provider's SSH key via `get_secret()`.

### The `bootstrap-server.sh` chicken-and-egg with helpers

Spec [03-bootstrapping.md](../spec/03-bootstrapping.md#files-that-must-already-be-on-the-server)
says helpers and the unit file must be on the server **before** the bootstrap
script runs (because the bootstrap script does `systemctl daemon-reload` and
the unit needs to exist by then). We follow that exactly: step 3 above
uploads them; step 4 runs the bootstrap; the daemon-reload at the end of the
script picks up the unit file.

`bootstrap-server.sh` itself does **not** know about helpers. Good. The
caller (us) is responsible.

## Test plan

### Unit tests

- `test_server_provider_test_connection_ok`: mock DO client `account()` →
  whitelisted method returns ok.
- `test_server_provider_test_connection_bad`: mock raises → method raises.
- `test_provision_server_inserts_server_and_enqueues`: mock
  `create_droplet`, assert Server row created with `Pending` status, assert
  `frappe.enqueue` called once with `finish_provisioning` and the right args.
- `test_finish_provisioning_happy_path`: mock `wait_for_active` and
  `Server.bootstrap`, assert end status `Active`.
- `test_finish_provisioning_bootstrap_fails`: mock bootstrap raises, assert
  end status `Broken` and exception bubbles.
- `test_carve_virtual_machine_range`: a few /64 inputs.
- `test_bootstrap_uploads_helpers_then_runs_script`: mock `upload_files` and
  `run_task`, assert call order.
- `test_bootstrap_parses_trailing_key_values`: mock `run_task` returning a
  Task with the canonical stdout shape, assert Server fields populated.

### E2E (`tests/e2e/phase_3.py`)

1. Pre-sweep.
2. Use an existing Provider row (`atlas-e2e-provider`) or create one from
   env-config.
3. Call `frappe.get_doc("Server Provider", "atlas-e2e-provider").provision_server("atlas-e2e-phase3")`.
4. Block on the enqueued job: poll the Server row's status until
   `Active` or `Broken`, timeout 5 minutes.
5. Assert: status=Active, `firecracker_version` populated, at least one
   Task with `script=bootstrap-server.sh` and `Success`.
6. SSH directly (using the same `run_task_on_server` with a probe script)
   and assert `/var/lib/atlas/bin/vm-network-up.sh` exists and is mode 0755.
7. **Re-run bootstrap** (`Server.bootstrap()` again): assert it succeeds
   (idempotency check).
8. `finally`: delete the droplet via the DO client. **Do not** rely on a
   "Delete Server" button (not built yet); use the DO client directly.

## What we are NOT doing in this phase

- No **Delete Server** / **Archive** button. To clean up, the operator deletes
  the droplet in the DO console and archives the row by hand. Cheap to add
  later; not on the critical path of the iteration's goals.
- No **Reboot** behavior (button is wired but is a stub that prints "TODO").
- No **Run Task** dialog (stub).
- No Virtual Machine doctype or list child-table on the Server form (phase 5
  adds the table).
- No image sync (phase 4).
- No live status reconciliation. The Server row's `status` reflects what
  Atlas last set; we don't poll the droplet to update it.
- No DO project assignment.
- No live tail of bootstrap logs in Desk — operator opens the Task row to
  see stdout when it's done.

## Spec drift introduced

See [drift.md](./drift.md#phase-3):

- Spec implies `provider_resource_id` is set "after" the Server row exists,
  but in our flow we have the droplet ID before we insert. We set it on
  insert. Minor.
- The Provider doc's defaults (region/size/image) are read into the Server
  row at insert. The spec is ambiguous about whether the Server has its own
  region/size or just references the provider. We materialize on the Server
  so the row is self-describing — matches the wireframe field list.
- `ipv6_address` on Server: the spec says "the host's ::1 of /64." DO
  actually gives us a specific address (could be `::1` or some other). We
  store **what DO gave us**, not synthesize `::1`. Matches reality.
