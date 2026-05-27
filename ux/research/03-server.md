# Server

Screenshots:
[desk-06-server-list.png](./screenshots/desk-06-server-list.png),
[desk-07-server-form.png](./screenshots/desk-07-server-form.png),
[desk-08-run-task-dialog.png](./screenshots/desk-08-run-task-dialog.png)

## Equal-weight top-bar buttons hide intent

`Bootstrap`, `Run Task`, `Reboot` all live in the same top bar with no
grouping. Bootstrap is a one-time setup (you probably never click it
again on the same server); Reboot is occasional; Run Task is an escape
hatch. They should not have equal visual weight.

## No "console" / "live view" / "what's running right now?"

The form shows infrastructure facts (IPv4, IPv6, region, size) and
back-links to VM and Task children. There's no panel showing recent task
activity inline — the operator has to click into Task and filter.

## `Run Task` dialog is the worst offender in the app

It exposes 9 scripts in a flat Select:

```
bootstrap-server.sh
provision-vm.sh
reboot-server.sh
start-vm.sh / stop-vm.sh / terminate-vm.sh
sync-image.sh
vm-network-up.sh / vm-network-down.sh
```

Operator-facing reality: **only `bootstrap-server.sh` and maybe
`reboot-server.sh` belong here.** Everything else is internal — the VM
doctype's own buttons should be the only way to invoke `provision-vm.sh`
etc. Right now the operator can fire `terminate-vm.sh` from this menu
against any server, with `Variables (JSON): {}`, and get an opaque shell
error. (I saw exactly that — the failed task at `Task/8k6u4v3bi1` was a
hand-fired `provision-vm.sh` with empty variables, dying at
`line 20: VIRTUAL_MACHINE_NAME: required`.)

## `Variables (JSON)` is a raw textarea

No schema, no per-script form, no hint of what keys each script wants. If
the operator picks `provision-vm.sh` the dialog should morph into a
VM-link picker, not demand they hand-author JSON.

Spec §README "Desk-button coverage" already acknowledges this footgun —
the test suite has to cover the JSON-string vs dict path because it's so
easy to break.

## `Reboot` has no confirmation

One click, a production VM host restarts. At minimum it should show
"this will reboot Server X (running N virtual machines) — type the server
name to confirm".

## Sidebar Operations panel counts are misleading

It shows `Virtual Machine 4` and `Task 9`. The Task count is a
session-wide count, not "currently in-flight" — useless at a glance.
