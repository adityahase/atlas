# Virtual Machine Image

Screenshots:
[desk-11-vm-image-list.png](./screenshots/desk-11-vm-image-list.png),
[desk-12-vm-image-form.png](./screenshots/desk-12-vm-image-form.png),
[desk-13-sync-to-server-dialog.png](./screenshots/desk-13-sync-to-server-dialog.png)

## `Sync to Server` is a bare Link picker

It shows `+ Create a new Server` as an option in the dropdown. Creating a
server inline from an image-sync dialog is a multi-hundred-dollar slip of
the wrist.

## `Sync to All Servers` has no confirmation

And no preview of *how many* servers it will hit.

## No sync status

The form has no field that says "this image is currently on these
servers". The operator has to grep Task history. The spec says "the
Frappe site is the source of truth" — but the source of truth here is
the audit trail of past sync tasks, not a denormalised
"synced_on_servers" panel. Should be one.

## Kernel/Rootfs URLs + SHA-256 are exposed as plain Data fields

Editable post-creation? If yes, that's a footgun (changing the SHA after
sync silently invalidates the audit). If no, they should be greyed out
clearly.
