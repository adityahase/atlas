# Desk UX research — 2026-05-27

A walkthrough of the operator path on the desk (Atlas workspace), captured
via Playwright against `http://atlas.local:8007`. Notes are framed as
**what hurts** and **why**, not as proposed code changes.

The walkthrough covered the full operator path: Server Provider → Provision
Server → Server → Run Task → Reboot → VM Image → Sync to Server → VM list →
VM (Pending / Terminated / new) → Task list → Task detail (success and
failure).

## Files

- [01-workspace.md](./01-workspace.md) — desk landing + Atlas workspace
- [02-server-provider.md](./02-server-provider.md) — Server Provider list, form, Provision dialog
- [03-server.md](./03-server.md) — Server list, form, Run Task, Reboot
- [04-virtual-machine-image.md](./04-virtual-machine-image.md) — VM Image list, form, Sync dialogs
- [05-virtual-machine.md](./05-virtual-machine.md) — VM list, lifecycle forms, create form
- [06-task.md](./06-task.md) — Task list and detail (audit trail)
- [07-cross-cutting.md](./07-cross-cutting.md) — issues that span surfaces
- [08-where-to-start.md](./08-where-to-start.md) — the three highest-leverage fixes

Screenshots referenced by each file live in [screenshots/](./screenshots/),
numbered in the order they were captured during the walkthrough.

## Method

- Logged in as `Administrator` against the running bench on port 8007.
- Used the existing `bootstrap-provider` and `bootstrap-server-1779879805`
  created by `atlas/bootstrap.py`, plus four VMs (3 Pending, 1 Terminated)
  and 9 Tasks (8 Success, 1 Failure) already on the site.
- Did not provision new resources — the failure modes were already
  visible in the existing state.
