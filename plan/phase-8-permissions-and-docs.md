# Phase 8 — Permissions, docs, and handoff

## Goal

Lock down permissions, write the README that points future contributors
at the right files, and ship a single bench command that runs all e2e
phases against one shared droplet for cheap regression checks.

After this phase, Atlas is the spec's "iteration 1" complete: a usable
building block that the next layer (Sites/Benches/IAM/Billing) can be
built on top of.

## You can do this at the end

```
bench --site atlas.local execute atlas.tests.e2e.run_all
```

Output:

```
phase-2 (DO client):           OK in 18s
phase-3 (bootstrap):           OK in 84s
phase-4 (image sync):          OK in 312s
phase-5 (vm provision):        OK in 11s
phase-6 (vm lifecycle):        OK in 13s
phase-7 (run task + reboot):   OK in 72s
Total: 510s. One droplet used + cleaned up.
```

Phase 1's e2e (which created a throwaway droplet just to validate `run_task`)
is folded into phase 3's flow — the first action against the shared droplet
**is** a `run_task` call.

## Files added or changed

### DocTypes — permissions

Each of the five `.json` files gets a `permissions` block:

```json
"permissions": [
    {
        "role": "System Manager",
        "read": 1, "write": 1, "create": 1, "delete": 1,
        "submit": 0, "cancel": 0, "amend": 0,
        "report": 1, "export": 1, "import": 0, "share": 1
    }
]
```

For `Task`: same except `delete: 0` (audit log, never delete from UI).
`write: 0` after insert is enforced by the controller, not the role.

### Module additions

- `atlas/atlas/tests/e2e/__init__.py` — add `run_all()` that orchestrates
  the per-phase runners against one shared droplet.

### Docs

- `atlas/README.md` — replace with a real top-level README. Spec link,
  install instructions, "how to verify locally" link, the one-paragraph
  "what Atlas does."
- `atlas/llm/CLAUDE.md` — add a line pointing Claude at the plan and at
  drift.md, the way the spec is currently referenced.

## `run_all` orchestration

```python
def run_all() -> None:
    client = get_client()
    sweep_old_droplets(client)

    droplet = create_test_droplet(client, "e2e-allphases")
    try:
        provider = ensure_test_provider(client, droplet)
        server = provision_server_from_droplet(provider, droplet, "e2e-allphases")

        results = []
        results.append(("phase-3", run_phase_3_against(server)))
        results.append(("phase-4", run_phase_4_against(server)))
        results.append(("phase-5", run_phase_5_against(server)))
        results.append(("phase-6", run_phase_6_against(server)))
        results.append(("phase-7", run_phase_7_against(server)))

        print_summary(results)
    finally:
        cleanup_droplet(client, droplet["id"])
        cleanup_test_provider(provider)
```

Each `run_phase_N_against(server)` is a refactor of phase N's existing e2e
that takes an existing server instead of provisioning one. This is the
shared-droplet optimization.

Phase 1's standalone e2e remains for the case where SSH plumbing breaks
in isolation; it's still invoked by `phase_1.run()` for debugging.

## Permissions hardening

After this phase, the only role that can touch any Atlas DocType is
`System Manager`. The roadmap's "ownership/teams" work happens in the layer
above Atlas (Sites/IAM); Atlas stays role-flat.

`get_secret()` continues to be the only path to decrypted passwords. We add
a tiny test that the SSH private key is **not** visible in the Server
Provider form via the REST API for any user. (One whitelist test;
defense-in-depth.)

## Top-level README

```
# Atlas

Atlas manages Firecracker virtual machines on servers. It is the lowest layer
of a Frappe hosting platform; sites, benches, IAM, and billing live in
separate apps on top.

- Spec: [spec/](./spec/README.md)
- Plan and history of how it got built: [plan/](./plan/00-overview.md)
- Shell scripts that run on the server: [scripts/](./scripts/README.md)

## What's here

- `atlas/` — the Frappe app source.
- `scripts/` — shell scripts uploaded over SSH and executed on the server.
- `spec/` — operator-facing specification.
- `plan/` — phased implementation plan (with `drift.md`).
- `llm/` — Claude-facing reference material.

## Local verification

After `bench install-app atlas` and creating an `atlas.local` site:

1. Put a DigitalOcean API token + SSH key fingerprint in the site config:

       bench --site atlas.local set-config -p atlas_do_token <DO_TOKEN>
       bench --site atlas.local set-config -p atlas_ssh_key_id <FINGERPRINT>
       bench --site atlas.local set-config -p atlas_ssh_private_key "$(cat ~/.ssh/atlas-test)"

2. Run the all-phases e2e:

       bench --site atlas.local execute atlas.tests.e2e.run_all

The run takes ~9 minutes and creates exactly one billable droplet (deleted
when the run ends).
```

## CLAUDE.md update

Append:

```
The plan lives in plan/. Read plan/00-overview.md and plan/drift.md before
touching anything in this app. drift.md is the list of unresolved spec
discrepancies; resolve as you go.
```

## Test plan

### Unit tests

- `test_only_system_manager_can_read_server_provider`: insert a
  `Server Provider`, switch user to a freshly-created basic user with no
  roles, assert `frappe.get_doc("Server Provider", ...)` raises
  PermissionError.
- `test_ssh_private_key_not_in_get_doc_response`: assert the dict returned
  by `frappe.client.get` does not contain the private key field's plaintext.
- `test_task_delete_blocked`: System Manager attempts `task.delete()`,
  assert raise.

### E2E

- `tests/e2e/run_all.py` — the orchestrator above. Asserts every phase
  passes against the shared droplet.

## What we are NOT doing in this phase

- No multi-tenant permissions. One role.
- No "Atlas Admin" role split from "System Manager." Future work, when the
  upper layer demands it.
- No audit-log export beyond what Desk gives us (Task list view + Frappe's
  built-in version tracking is disabled because Task is `track_changes = 0`).
- No published artifacts. The app installs from this repo; no PyPI package,
  no docker image.
- No CI configuration. Operator runs `run_all` by hand for now.

## Spec drift introduced

None new. Phase 8 is hardening + docs.

## Resolving accumulated drift

Read [`drift.md`](./drift.md). Either:

1. Update the spec to match the implementation we shipped.
2. Update the implementation to match the spec.

Phase 8 is the natural moment to do (1) for the items where the
implementation is obviously the better choice (UUID for VMs, hash for Tasks,
the `scripts_catalog` hookpoint), and to leave items requiring a real
discussion for an explicit follow-up.

The final to-do at the end of phase 8 is: **walk drift.md with the operator,
mark each item resolved (spec or code updated) or punted.**
