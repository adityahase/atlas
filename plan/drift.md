# Spec drift

Per-phase list of places the implementation plan deviates from the spec or
makes a choice the spec didn't pin. Each entry: the spec location, the
implementation choice, and a "resolve by" suggestion. Walk this with the
operator at the end of phase 8.

---

## Phase 1

### 1.1 Task `name` is `hash` not "UUID"

- Spec: [`spec/02-doctypes.md:281`](../spec/02-doctypes.md#task) says
  `name = (autoname hash)` with the note "UUID."
- Implementation: Frappe's `autoname = "hash"` produces a 10-char random
  hex string, not a UUID. We use it as-is.
- Resolve: update the spec to say "10-char hash" — they're interchangeable
  for audit rows.

### 1.2 `run_task(server, ...)` vs `run_task(connection, ...)`

- Spec: [`spec/04-tasks.md:28`](../spec/04-tasks.md) says `run_task(server,
  script, variables, virtual_machine=None)`.
- Implementation: `run_task(connection={...}, ...)` is the low-level
  primitive; `run_task_on_server(server, ...)` (phase 3) is the convenience
  wrapper that builds the dict from a Server doc.
- Resolve: keep both. Document the wrapper in `04-tasks.md`.

### 1.3 No reconciler for orphaned `Running` Task rows

- Spec doesn't address this case directly.
- Implementation: if the worker dies between "set Running" and the final
  update, the row stays Running. We don't recover.
- Resolve: log this in `09-roadmap.md` as "next-iteration: stuck-task
  reaper." Acceptable for iteration 1.

---

## Phase 2

No drift. Spec doesn't describe the DO client at this level.

---

## Phase 3

### 3.1 `Server.provider_resource_id` set on insert, not after

- Spec: [`spec/02-doctypes.md:68`](../spec/02-doctypes.md#server) implies
  this is set later.
- Implementation: we have the droplet ID before the Server insert; we set
  it then.
- Resolve: no change needed. Spec is ambiguous; we picked the obvious
  order.

### 3.2 `Server.region` and `Server.size` materialized on the row

- Spec: [`spec/02-doctypes.md:72`](../spec/02-doctypes.md#server) shows
  them as Server fields. Doesn't say where they come from.
- Implementation: copied from the Provider's defaults at insert time. Form
  shows them read-only.
- Resolve: spec is consistent with our choice. No change.

### 3.3 `Server.ipv6_address` = "host ::1" vs whatever DO gives us

- Spec: [`spec/02-doctypes.md:74`](../spec/02-doctypes.md#server)
  parenthetical reads "host's ::1 of /64."
- Implementation: we store the actual public v6 address DO assigns. In
  practice it's `::1` of the /64, but if DO ever gives us a different one,
  we record the truth.
- Resolve: amend spec parenthetical to "typically `::1` of the /64; whatever
  DO assigns."

### 3.4 `/124` carve-out: first /124 of the /64

- Spec: [`spec/06-networking.md:18`](../spec/06-networking.md) says "only the
  first /124 is actually routable inside DO's fabric."
- Implementation: `carve_virtual_machine_range(prefix_cidr)` returns the
  first /124 of the /64. Assumed.
- Resolve: verify on a real droplet (phase 3 e2e implicitly does this). If
  DO's behavior changes, the function changes.

### 3.5 Bootstrap helpers uploaded by `Server.bootstrap()`, not by a separate Task

- Spec: [`spec/03-bootstrapping.md:42`](../spec/03-bootstrapping.md)
  says "uploading them is the caller's job, so that we keep the contents
  of `atlas/scripts/` as the single source of truth." It also says the
  pre-copy step is "not a Task."
- Implementation: matches exactly. `Server.bootstrap()` calls
  `upload_files()` (not a Task) then `run_task_on_server()` (one Task).
- Resolve: nothing to do. Pinning this here so we don't drift later.

---

## Phase 4

### 4.1 `GUEST_NETWORK_UNIT` upload formalized via `script_uploads.py`

- Spec: [`spec/08-images.md:38`](../spec/08-images.md) says the guest unit
  is "uploaded to the server alongside `sync-image.sh` before the script
  runs."
- Implementation: a `SCRIPT_UPLOADS` map in `script_uploads.py`. Every
  script declares its sidecar uploads.
- Resolve: amend `08-images.md` to point at this hookpoint.

### 4.2 No concurrent-sync guard

- Spec doesn't say.
- Implementation: two concurrent syncs of the same image-on-server are a
  race. We don't guard.
- Resolve: add to `09-roadmap.md` as "Server lock doctype" follow-up.

### 4.3 ext4 size assertion in e2e

- Spec doesn't say what the resulting ext4 should look like beyond "of
  `default_disk_gigabytes`."
- Implementation: e2e asserts ext4 file size is within 5% of nominal.
- Resolve: nothing.

---

## Phase 5

### 5.1 VM `name` set in `before_insert` with `uuid.uuid4()`, not `autoname`

- Spec: [`spec/02-doctypes.md:146`](../spec/02-doctypes.md#virtual-machine)
  says `autoname` on insert.
- Implementation: Frappe's `autoname` doesn't produce UUIDs out of the box;
  `before_insert` is the standard way. Functionally equivalent.
- Resolve: amend the spec hint.

### 5.2 `last_started` set on Provision (not just on Start)

- Spec: [`spec/05-virtual-machine-lifecycle.md:74`](../spec/05-virtual-machine-lifecycle.md)
  says Provision ends with `status = Running`, `last_started = now()`.
- Implementation: matches exactly. Pinning so phase 6's `start()` doesn't
  forget to update it.
- Resolve: nothing.

### 5.3 IPv6 allocator: skip `::0` and `::1`

- Spec: [`spec/06-networking.md:42`](../spec/06-networking.md) says `::1` is
  the host, addresses start at `::2`. Doesn't mention `::0`.
- Implementation: `ipaddress.IPv6Network.hosts()` already excludes `::0`
  for non-/127 subnets, so the explicit `index < 2` skip only excludes
  `::1`. Test pins behavior.
- Resolve: nothing.

### 5.4 Provision requires image already on server (does not auto-sync)

- Spec: [`spec/05-virtual-machine-lifecycle.md:71`](../spec/05-virtual-machine-lifecycle.md)
  says "Ensure the image is on the server. If not, run sync-image.sh (this
  is its own Task; provisioning waits on it)."
- Implementation (resolved 2026-05-25 with operator): **Provision fails
  fast if the image is absent.** It does not enqueue or wait for a sync.
  The operator runs **Sync to Server** explicitly (a multi-minute action),
  then Provision (which becomes fast and predictable).
- Resolve: amend [`spec/05-virtual-machine-lifecycle.md:71`](../spec/05-virtual-machine-lifecycle.md)
  to say "Verify the image is on the server. If not, fail with a clear
  error pointing the operator at Sync to Server."

---

## Phase 6

### 6.1 `delete-vm.sh` Python wrapper called `delete_vm`, not `delete`

- Spec: [`spec/02-doctypes.md:170`](../spec/02-doctypes.md#virtual-machine)
  says button label "Delete."
- Implementation: button label is "Delete"; the Python method is
  `delete_vm` to avoid colliding with `frappe.model.document.Document.delete`.
- Resolve: nothing. Button label is what the operator sees.

### 6.2 Status change to `Archived` happens in Python (not in the script)

- Spec: [`spec/05-virtual-machine-lifecycle.md:111`](../spec/05-virtual-machine-lifecycle.md)
  says "Then Python sets `status = Archived`."
- Implementation: matches. Pinning.
- Resolve: nothing.

### 6.3 Failed `Delete` does not archive

- Spec doesn't specify.
- Implementation: status updates only on successful Task. Operator clicks
  Delete again (idempotent).
- Resolve: amend spec to clarify.

---

## Phase 7

### 7.1 `reboot-server.sh` added as a real script

- Spec: [`spec/02-doctypes.md:96`](../spec/02-doctypes.md#server) says
  "Reboot — `systemctl reboot` over SSH."
- Implementation: a one-line shell script under `scripts/reboot-server.sh`,
  invoked via the standard Task path. Keeps everything uniform.
- Resolve: nothing.

### 7.2 `scripts_catalog.allowed_scripts()` enumerates the directory

- Spec: [`spec/04-tasks.md:163`](../spec/04-tasks.md) says the dialog has "a
  picker over the scripts directory."
- Implementation: a Python function that lists `.sh` files at
  `scripts/*.sh` (not `scripts/guest/`, not `scripts/systemd/`). Whitelist.
- Resolve: nothing.

### 7.3 Reboot Task ends in Failure (SSH drops)

- Spec doesn't address.
- Implementation: expected outcome. Operator-visible Failure with the
  understood meaning "the server is rebooting." E2E asserts the server
  comes back.
- Resolve: amend spec to call this out.

---

## Phase 8

No new drift introduced. Phase 8 is permissions + docs.

---

## How to use this file

When implementing a phase:

1. Re-read the relevant section here before writing code.
2. If you introduce **new** drift, add an entry to that phase's section
   immediately, before commit.
3. At phase 8, walk through with the operator and either update the spec
   or update the code. Each entry becomes either "spec was right, code
   updated" or "code was right, spec updated."

This file is not a TODO list. It is a contract: every drift must end the
iteration as either resolved or explicitly punted to the next iteration's
roadmap.
