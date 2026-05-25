# Phase 1 — SSH plumbing + Task DocType

## Goal

Land the SSH execution module and the Task DocType. By the end of this phase,
any Python code in Atlas can call `run_task(connection, script, variables)` and
get a persisted, replayable `Task` row out the other end. No other DocTypes
exist yet. No buttons. Just the primitive.

The chicken-and-egg with `Server`: `run_task` takes a **connection dict** in
this phase. Phase 3 introduces `Server` and a one-line helper that builds the
dict from a Server document.

## You can do this at the end

```python
# bench --site atlas.local console
from atlas.atlas.ssh import run_task

task = run_task(
    connection={
        "host": "165.232.x.y",
        "ssh_private_key": open("~/.ssh/id_ed25519").read(),
        "user": "root",
    },
    script="bootstrap-server.sh",
    variables={"FIRECRACKER_VERSION": "v1.15.1", "ARCHITECTURE": "x86_64"},
)
print(task.name, task.status, task.exit_code)
```

A Task row appears in Desk under **Atlas > Task** with stdout/stderr captured.

## Files added

### Module scaffolding

- [`atlas/atlas/atlas/__init__.py`](../atlas/atlas/atlas/__init__.py) — already
  exists, leave alone.
- `atlas/atlas/atlas/ssh.py` — the only new module. ~150 lines.
- `atlas/atlas/atlas/secrets.py` — single function. ~15 lines.

### DocType

- `atlas/atlas/atlas/doctype/task/__init__.py`
- `atlas/atlas/atlas/doctype/task/task.json`
- `atlas/atlas/atlas/doctype/task/task.py` — minimal controller (no buttons,
  read-only after insert validation).
- `atlas/atlas/atlas/doctype/task/test_task.py`

### Tests

- `atlas/atlas/tests/__init__.py`
- `atlas/atlas/tests/e2e/__init__.py`
- `atlas/atlas/tests/e2e/_shared.py` — minimal helpers for phase 1
  (reads connection details from site config). Phase 2 will expand this
  with DO-client-backed helpers.
- `atlas/atlas/tests/e2e/phase_1.py`

## Task DocType schema

Field list from
[`../spec/02-doctypes.md`](../spec/02-doctypes.md#task) — implemented exactly.

Specifics for this phase:

- `naming_rule = "Random"`, `autoname = "hash"` → 10-char hash. Matches the
  spec's "(autoname hash) UUID" note. (The spec calls it UUID but `hash`
  produces a 10-char string; rename in [drift.md](./drift.md).)
- `server` and `virtual_machine` Link fields **must not have Link Filters or
  on-delete cascades** for now — the linked doctypes don't exist yet.
  Frappe accepts forward-Link fields whose target doesn't yet exist; a
  patch in phase 3/5 will validate.
- `variables`: `Long Text`, JSON-serialized. Validation in
  `task.py::validate()` parses it as JSON or raises.
- `stdout`, `stderr`: `Code` fields with `read_only = 1`.
- `status` default `"Pending"`.
- All fields except `status`, `exit_code`, `stdout`, `stderr`, `ended`,
  `duration_milliseconds` are `read_only = 1` after first insert (set via
  `task.py::on_update()`'s `if not self.is_new(): ...` guard).
- Indexes per spec: `server`, `virtual_machine`, `status`, `script`.
- `track_changes = 0`. We log everything through the row itself.

## `atlas/atlas/atlas/ssh.py` — the module

Public surface:

```python
def run_task(
    connection: dict,
    script: str,
    variables: dict,
    virtual_machine: str | None = None,
    server: str | None = None,
    timeout_seconds: int = 1800,
) -> "Task": ...

def execute_task(task_name: str) -> None:
    """Background-job entrypoint. Reads an existing Pending Task row, runs it,
    updates it. Called by frappe.enqueue from phases 3 and 4."""

def upload_files(connection: dict, files: list[tuple[str, str]]) -> None:
    """scp files to the server. (local_path, remote_path) pairs.
    Not recorded as a Task. Called by Server.bootstrap() in phase 3."""
```

Private surface (internal):

```python
def _open_ssh_session(connection: dict) -> "_SshContext": ...
def _run_remote_script(ctx, script, variables, timeout) -> tuple[str, str, int]: ...
def _scp(ctx, local, remote) -> None: ...
```

Behavior pinned by the spec ([`../spec/04-tasks.md`](../spec/04-tasks.md#how-it-runs)):

- Use `subprocess.run(["ssh", ...])` and `["scp", ...]` — no paramiko.
- Write `ssh_private_key` to a `tempfile.NamedTemporaryFile(mode=0o600)`, pass
  via `-i`. Delete it on exit (`finally`).
- SSH options:
  ```
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile=~/.atlas/known_hosts
  -o BatchMode=yes
  -o ConnectTimeout=30
  ```
  Mkdir `~/.atlas/` (mode 0700) on the bench host if absent.
- Variables passed as `env VAR=val ... bash -x /tmp/atlas/script.sh`, quoted
  with `shlex.quote()`.
- Upload script to `/tmp/atlas/` via `scp` (one process) before running.
- `_run_remote_script` returns `(stdout, stderr, returncode)`. `run_task` and
  `execute_task` translate non-zero returncode into Task `status = Failure`
  and raise `frappe.ValidationError`.

### Failure model

- `subprocess.TimeoutExpired` → `status = Failure`,
  `stderr = "Timed out after {N}s"`, re-raise as `frappe.ValidationError`.
- SSH connect failure (returncode 255) → same, `stderr` contains ssh's own
  error.
- Script non-zero exit → `status = Failure`, full stdout/stderr captured,
  `exit_code` set, raise `frappe.ValidationError`.
- On success: `status = Success`, `exit_code = 0`, `ended = now()`,
  `duration_milliseconds` computed.

### Idempotency on the row

`run_task` inserts a row in `validate()`-friendly state (`Pending`), flips to
`Running` right before the subprocess, then to `Success`/`Failure`. If the
worker dies between Running and the final update, the row stays `Running`
forever — that's acceptable for the iteration (operator can re-trigger by
clicking the button; new Task row is created). No reconciler in this phase.

## `atlas/atlas/atlas/secrets.py`

```python
import frappe
from frappe.utils.password import get_decrypted_password


def get_secret(doctype: str, name: str, fieldname: str) -> str:
    """Read a Password-type field, decrypted. Single chokepoint so the
    storage backend can be swapped later."""
    return get_decrypted_password(doctype, name, fieldname, raise_exception=True)
```

Used everywhere a secret is read. No caller talks to
`frappe.utils.password.*` directly.

## Test plan

### Unit tests (`atlas/atlas/atlas/doctype/task/test_task.py`)

- `test_task_insert_defaults`: insert with required fields, assert defaults.
- `test_task_variables_must_be_json`: insert with `variables = "{not json"`,
  assert `ValidationError`.
- `test_task_immutable_after_insert`: insert a Task, modify
  `script`/`variables`, assert `ValidationError` on save.

### Unit tests (`atlas/atlas/tests/test_ssh.py`, new file)

Mock `subprocess.run`:

- `test_run_task_success`: mock returns `(stdout="ok\n", stderr="", rc=0)`.
  Assert Task row Success, exit_code 0.
- `test_run_task_failure`: mock returns rc=2 plus stderr. Assert Failure,
  exit_code 2, raises `ValidationError`.
- `test_run_task_timeout`: mock raises `TimeoutExpired`. Assert Failure,
  stderr contains "Timed out".
- `test_run_task_writes_private_key_temp_file_and_deletes_it`: assert no key
  file remains in `/tmp/` after the call.
- `test_variables_quoted_with_shlex`: assert the constructed command has
  `VAR='value with spaces'`.

### E2E (`atlas/atlas/tests/e2e/phase_1.py`)

Phase 1's e2e runs against an **operator-provided, manually-configured
droplet**. No DO API calls — phase 2 lands the API client. The operator
spins up a vanilla Ubuntu 24.04 droplet by hand, puts an SSH key on it, and
records the connection details in the site config:

```
bench --site atlas.local set-config -p atlas_phase1_host <ipv4>
bench --site atlas.local set-config -p atlas_phase1_ssh_private_key "$(cat ~/.ssh/atlas-test)"
```

`tests/e2e/_shared.py` reads these as `get_phase1_connection() -> dict`.

Steps:

1. Read connection from site config. If missing, print clear instructions
   and exit non-zero.
2. Build the connection dict: `{host, ssh_private_key, user: "root"}`.
3. Call `run_task(connection, script="phase1-probe.sh", variables={"NAME": "hi"})`.
   `phase1-probe.sh` is a one-liner: `echo "hello $NAME"; exit 0`.
   It lives at `atlas/atlas/tests/e2e/scripts/phase1-probe.sh`.
4. Assert: Task row exists, status=Success, exit_code=0,
   stdout contains `hello hi`.
5. Call a second time with a script that fails:
   `echo "boom" >&2; exit 7`. Assert: Task row status=Failure,
   exit_code=7, stderr contains `boom`.
6. Call a third time with a non-existent script. Assert clean error
   (no half-written Task row).

The droplet stays around — the operator deletes it manually when phase 2
lands, and the e2e harness from phase 2 onward creates its own throwaway
droplets.

No `try/finally` cleanup needed: this phase doesn't create anything on DO.

Bench invocation:

```
bench --site atlas.local execute atlas.tests.e2e.phase_1.run
```

## Ordering note

Phase 1 is fully self-contained — unit tests + e2e against a manual droplet.
Phase 2's DO client lands afterward and replaces the manual-droplet workflow
for every subsequent phase with auto-created throwaway droplets. The
operator can delete the phase-1 manual droplet at that point.

## What we are NOT doing in this phase

- No `Server` doctype. `run_task` takes a connection dict, not a Server name.
- No buttons anywhere.
- No `frappe.enqueue` wiring (phase 3 onward).
- No `Run Task` UI (phase 7).
- No host-key pinning. `accept-new` and a per-bench `~/.atlas/known_hosts`.
- No retries. One shot, fail loud.
- No stdout/stderr spill-to-file. Goes straight into the Code field.

## Spec drift introduced

See [drift.md](./drift.md#phase-1):

- Spec says Task `name` is "UUID" but the doctype hint is `(autoname hash)`
  which is 10 chars. We pick `hash` (matches existing convention) and update
  the spec note to "10-char hash, treated as a stable surrogate key."
- Spec says `run_task(server, ...)` everywhere; in phase 1 it's
  `run_task(connection={...}, ...)`. Phase 3 adds a thin
  `run_task_on_server(server, ...)` wrapper that calls `run_task` with a
  connection dict built from the Server doc.
