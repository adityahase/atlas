# Task — the audit trail UX

Screenshots:
[desk-09-task-list-for-server.png](./screenshots/desk-09-task-list-for-server.png),
[desk-10-failed-task-top.png](./screenshots/desk-10-failed-task-top.png),
[desk-19-task-bootstrap-success.png](./screenshots/desk-19-task-bootstrap-success.png)

## Task IDs are random strings, with no human-readable subject

Random slugs like `8k6u4v3bi1`, `tme8q7trdq` are fine for primary keys,
terrible for breadcrumbs. There's no human-readable subject like
"Provision verify-vnet-hdr-fix on bootstrap-server" — the operator has
to mentally join `script` + `virtual_machine` + `server` columns every
time.

## The Task form is just the DocType editor

No timeline, no diff between input and result, no link to "next task
this triggered". For a failed task you can read stderr and that's it.
You can't:

- rerun the task (no Retry button)
- see the actual script source as it was uploaded
- jump to the VM that owns the task (the `virtual_machine` field is
  present in the schema but wasn't visible for the failed
  `provision-vm.sh` I inspected)
- see structured error info (the `Exit Code: 1` field is buried at the
  top right with no emphasis even though it's the whole story)

## Stdout and Stderr are tiny clipped textareas at the bottom

The bootstrap task had 8.8 KB of stdout and 2.9 KB of stderr — both
stuffed into ~5-line readonly boxes with manual scroll. No log viewer
affordances:

- no monospace colouring
- no search
- no wrap toggle
- no "open full log in new tab"
- no ANSI colour rendering
- no auto-tail while a task is still running

## No live status for in-flight tasks

A long-running bootstrap script (28s+) is a black box. The form doesn't
poll; you have to manually refresh to see status change from Running →
Success. There's no progress indicator, no "currently executing line X
of script Y" tail.

## Failed tasks leave the parent VM in `Pending` forever

I saw three `Pending` VMs on the server, all from tasks that failed
earlier. The VM form shows `Pending` but doesn't say "last provision
attempt failed — see Task tme8q7trdq". The operator has to dig.
