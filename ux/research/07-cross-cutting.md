# Cross-cutting issues

Issues that span every surface in the app.

## Desk's default DocType chrome is fighting Atlas

Every form has the right rail (Assign / Attachments / Tags / Share /
Last Edited By / Created By), bottom Comments panel, breadcrumb, sidebar
— none of which Atlas needs at this layer. The operationally-relevant
content (status, buttons, key fields) occupies maybe 50% of the screen
real estate.

## No primary-action / dangerous-action visual hierarchy

`Save`, `Provision`, `Terminate`, `Reboot`, `Test Connection`,
`Bootstrap` all render as nearly-identical dark or grey pills in the top
bar. Destructive actions should be red and confirmed; rare-but-safe
actions should be quieter than common ones.

## No confirmations on anything destructive or expensive

Provision Server, Sync to All Servers, Reboot, Terminate — all
one-click.

## No idea of "what's normal"

- No latency expectations on the dialogs ("provisioning takes ~90s").
- No progress indicator while a long action runs.
- No toast linking to the resulting record/task after a click.

## Operations and audit are separated

The Server form has back-links to Task and VM, but the Task list has no
back-link to the VM (the `virtual_machine` column is there but doesn't
render in the failed-task example). Operations are forward; audit should
be bidirectional.
