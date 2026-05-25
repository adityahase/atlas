# SSH & Commands

Every change to a metal node is one or more SSH commands. We do not push
configuration with an agent; we do not run an Atlas daemon on the host. The
only thing Atlas installs that *runs* on the host is systemd unit files and
two helper scripts. Everything else is a one-shot SSH invocation.

## Library

We use **paramiko**. Reasons:

- Pure Python, no system dependencies beyond OpenSSL.
- Already in many Frappe envs.
- Sufficient for one-shot `exec_command` against a known host.

We do not use Fabric, pyinfra, Ansible, or Mitogen — those are for fleets and
recipes. We have one host per command and want full control of the audit log.

Dependency added to `pyproject.toml`:
```
paramiko >= 3.4
```

## Connection details

- **User**: `root`.
- **Auth**: SSH private key stored on the `Metal Provider`. (Every node from a
  given provider uses the same key.)
- **Known hosts**: we use `AutoAddPolicy` for this iteration. We do not pin
  host keys. This is fine because the node IP came from the DO API call that
  *created* the droplet a few seconds earlier, but it is a known weakness —
  see [09-roadmap.md](./09-roadmap.md).
- **Timeouts**: 30s connect, 600s command. Anything longer than 10 minutes is
  almost certainly a bug.
- **No connection pooling.** We open a fresh SSH session per command. Atlas is
  not throughput-bound; a 200ms connect is fine.

## The execution wrapper

A single module, `atlas.atlas.ssh`, exposes one function:

```python
def run(node: "MetalNode",
        command: str,
        *,
        virtual_machine: str | None = None,
        timeout: int = 600,
        check: bool = True) -> "MetalCommand":
    """
    Open an SSH session to `node`, run `command`, capture stdout/stderr,
    create and return a saved `Metal Command` document.

    If `check` and exit_code != 0, raises `frappe.ValidationError` *after*
    saving the command. Callers can wrap in try/except to inspect the
    document.
    """
```

The function:

1. Inserts a `Metal Command` row with `status = Running`, `started_at = now()`.
2. Connects, calls `exec_command`, reads stdout/stderr fully.
3. Updates the row with the result, sets `status` to `Success` or `Failure`.
4. Commits before returning. (So even if the caller dies, the record persists.)

### Heredocs / file uploads

For commands that need a multi-line payload (e.g. writing
`vmconfig.json`), we use bash heredocs inside a single command string:

```bash
install -m 0644 /dev/stdin /var/lib/atlas/vms/vm-001/vmconfig.json <<'ATLAS_EOF'
{ ... json ... }
ATLAS_EOF
```

We do *not* use `SFTP` for small writes — keeping the data inside the command
string means the entire payload is captured in `Metal Command.command`, which
makes audit and replay trivial. (Large files like the kernel/rootfs are
downloaded by the *host* over HTTPS from the source URL — see
[08-images.md](./08-images.md) — never pushed from Frappe.)

### Quoting

The command string is built with Python's `shlex.quote()` for any
operator-supplied value (e.g. `Run Command` dialog input, VM names). The
JSON heredoc above uses a `'ATLAS_EOF'`-quoted terminator so the payload is
not subject to shell expansion.

## Idempotency vs transactionality

We do not have distributed transactions. We do have:

- **Idempotent commands** wherever feasible (`mkdir -p`, `ip link del ... || true`,
  `nft ... || true`).
- **Per-step Metal Command records**, so re-running a flow after a crash can
  see exactly which step succeeded.

If a multi-step flow fails mid-way (say, step 5 of provisioning), the doc
status flips to `Failed` and the operator presses `Delete` to clean up, then
re-creates. We do not attempt automatic rollback.

## "Run Command" — the escape hatch

`Metal Node` has a `Run Command` button. It opens a dialog with a single
multi-line text input. On submit, the input is passed verbatim to `ssh.run()`
and the result is shown in a follow-up dialog with stdout, stderr, and exit
code.

Use cases:

- Debugging without leaving Desk.
- One-off operations that don't justify a new DocType method.
- Verifying state after a failed Atlas operation.

The command is recorded as a `Metal Command` like any other, with
`triggered_by` = the operator, so we have a full audit trail of ad-hoc
fiddling.

## Permission model on the host

`root`. Everywhere. For this iteration.

Why: starting with `root` means we don't have to debug a `sudo` setup, a
`kvm` group membership, or capabilities. The cost is a wider blast radius if
the SSH key leaks. We accept that for the building block; tightening it is
on the [roadmap](./09-roadmap.md).

## Concurrency

Multiple background jobs can be processing different VMs on the same node at
the same time. Each opens its own SSH session. We rely on:

- The DB-level per-node serialization for **IPv6 allocation** (see
  [05-networking.md](./05-networking.md)).
- Distinct VM directories — `mkdir -p /var/lib/atlas/vms/{vm_name}` cannot
  collide because `vm_name` is the primary key.
- nftables's own handling of concurrent rule adds (it's safe; rules just
  pile up).

We do **not** serialize all commands on a node behind a single queue. That
would be simpler but unnecessarily slow once a node has many VMs.

## Failure recording

Every failure (non-zero exit, SSH timeout, connection refused) becomes a
`Metal Command` with `status = Failure`. The calling code is expected to:

1. Catch the exception.
2. Read the row to decide how to surface the error to the operator (the
   exception itself already contains the row's `name`).
3. Update the parent doc's status appropriately.

There is no separate "error log". The command log *is* the error log.
