# E2E testing guideline

> This is the going-forward guideline for end-to-end tests. The historical
> phase-N e2e plans ([phase-1-ssh-and-task.md](./phase-1-ssh-and-task.md) …
> [phase-8-permissions-and-docs.md](./phase-8-permissions-and-docs.md)) and
> the historical extension plan [e2e-coverage.md](./e2e-coverage.md)
> describe how the suite was first built; they are kept for context.
> Anything written today follows this file.

## Principle

**E2E tests are organized by operator use case, not by implementation
phase.** A use case is something an operator does in Desk: provision a
server, sync an image, provision a VM, operate a VM, run an ad-hoc task,
verify the DO client, use the SSH primitive directly. The happy path, the
operator-visible negative paths, and the validation throws that guard the
same DocType method all live in **one file**.

The implementation was sequenced in phases; the testing surface should
reflect the system the operator sees, not the order in which it was built.

## Layout

```
atlas/tests/e2e/
├── __init__.py                          # run_all, run_all_coverage
├── _config.py                           # site config / DEFAULT_IMAGE / keys
├── _droplets.py                         # droplet lifecycle, phase() ctx mgr
├── _image.py                            # ensure_default_image_row, …
├── _inspect.py                          # operator debugging helpers
├── _shared.py                           # re-export shim
├── _tasks.py                            # wait_for_task, expect_validation_error
├── scripts/                             # e2e-only probe / fail scripts
└── use_cases/
    ├── __init__.py
    ├── digitalocean_client.py           # DO API client round trip + errors
    ├── image_sync.py                    # Image -> Server sync + validation
    ├── run_task.py                      # run_task_dialog + reboot + Task DocType
    ├── server_provisioning.py           # Provider.provision_server + bootstrap
    ├── ssh_primitive.py                 # run_task(connection=…) / upload_files
    ├── virtual_machine_lifecycle.py     # start / stop / restart / terminate
    └── virtual_machine_provisioning.py  # VM provision + image-missing path
```

The seven use cases map to the operator-visible operations described in
the spec:

| Use case                          | Spec                                                          | Operator action                                  |
| --------------------------------- | ------------------------------------------------------------- | ------------------------------------------------ |
| `digitalocean_client`             | [01-architecture](../spec/01-architecture.md)                 | (internal) verify the DO HTTP client             |
| `ssh_primitive`                   | [04-tasks](../spec/04-tasks.md)                               | (internal) `run_task(connection=…)`              |
| `server_provisioning`             | [03-bootstrapping](../spec/03-bootstrapping.md)               | Provider → **Provision Server**                  |
| `image_sync`                      | [08-images](../spec/08-images.md)                             | Image → **Sync to Server / Sync to All**         |
| `virtual_machine_provisioning`    | [05-virtual-machine-lifecycle](../spec/05-virtual-machine-lifecycle.md) | VM → **Provision** (the create path) |
| `virtual_machine_lifecycle`       | [05-virtual-machine-lifecycle](../spec/05-virtual-machine-lifecycle.md) | VM → **Start / Stop / Restart / Terminate** |
| `run_task`                        | [04-tasks](../spec/04-tasks.md) + [02-doctypes](../spec/02-doctypes.md) | Server → **Run Task / Reboot**            |

## What a use case module owns

Each `use_cases/<use_case>.py` is the **single source of truth** for that
operation's end-to-end coverage. It includes:

1. **The happy path.** One full pass against a real server.
2. **The negative paths the operator can hit on this use case.** E.g. the
   image-missing path lives in `virtual_machine_provisioning` because that
   is the use case the operator triggers and where they need the error
   message; not in a separate "validation" module.
3. **The DocType-level validation throws** that guard the same method —
   immutability, required fields, JSON shape, state-machine guards. These
   belong with their use case because they are how the use case fails
   loudly to the operator.
4. **Synchronous-path coverage for the background jobs** that the use case
   normally enqueues (e.g. `image_sync` calls `execute_task` directly, not
   only through `frappe.enqueue`). This sidesteps worker-process
   instrumentation for coverage runs.

Bias toward adding a check to an existing use case. Add a new use-case
module only when the operator gets a new button.

## Entry points

- `run` — happy-path-plus-validation for one use case. The signature varies
  by use case:
  - Use cases that need a shared bootstrapped server:
    `run(reuse=True, keep=True)`. They use the `phase()` context manager.
  - Use cases that own their droplet semantics (`digitalocean_client`,
    `server_provisioning` fresh-provision, `ssh_primitive`'s operator
    droplet path): `run()` with no kwargs.
- `run_against_shared(reuse=True, keep=True)` — appears on use cases whose
  *primary* entry point owns its droplet but which also have validation or
  idempotency checks that piggyback on the shared server. Two of them have
  this shape today: `server_provisioning` (idempotency / duplicate name /
  test_connection / status guard) and `ssh_primitive` (transport branches /
  Server.bootstrap re-run).

## Orchestrators

- `run_all()` runs every use case that takes a server against **one shared
  droplet** (`reuse=True, keep=True`), then deletes the droplet at the end.
  This is the regression entry point.
- `run_all_coverage()` runs everything that contributes to e2e coverage —
  adds the dedicated-droplet use cases (`digitalocean_client.run`,
  `server_provisioning.run`). Cost: three billable droplets.

Use cases that aren't safe in a shared run are invoked directly via
`bench execute`. See the docstring on each module.

## When to add a new use case

You add a new file under `use_cases/` only when there is a **new operator
operation** — a new button, a new entry-point function, a new external
integration. If you are exercising a new branch of an existing operation,
extend the existing file.

The file's name should match the operator's mental model of the operation.
Avoid implementation-detail names ("validation", "negatives", "extensions").

## When to add new helpers

`_config.py` / `_droplets.py` / `_image.py` / `_tasks.py` are the shared
substrate. Add helpers there when at least two use cases would benefit.
Single-use helpers stay private to their use-case module.

## Coverage

`run_all_coverage()` produced 96 % line coverage at the end of iteration 1
(see [e2e-coverage.md](./e2e-coverage.md) for the historical breakdown).
The remaining missed statements are documented there and are intentional —
each costs more in test infrastructure than it returns in coverage.

When you add a use case, check whether it exercises new branches by
running `coverage run -p -m frappe.utils.bench_helper frappe --site
atlas.e2e.local execute atlas.tests.e2e.run_all_coverage` and inspecting
the diff against the prior `coverage.xml`. New code without new coverage
is a smell.
