# Virtual Machine

Screenshots:
[desk-14-vm-list.png](./screenshots/desk-14-vm-list.png),
[desk-15-vm-form-pending.png](./screenshots/desk-15-vm-form-pending.png),
[desk-17-vm-form-terminated.png](./screenshots/desk-17-vm-form-terminated.png),
[desk-18-vm-create-form.png](./screenshots/desk-18-vm-create-form.png)

## List headlines `Description` instead of a stable identifier

Three rows say `verify vnet_hdr fix`, `verify vnet_hdr fix`, `verify
carve fix` — the operator has no way to tell which physical VM is which
without clicking through. The IPv6 column is good but truncated.

## Pending VM form has no Networking section

A Pending VM has no IPv6 yet — but the spec says the IPv6 is allocated
from the server's range up front. If it's known, show it. If not, show
"address will be assigned at provision time" so the operator knows what
to wait for.

## Terminated VM form is identical to Pending

Same fields, no buttons, no "this VM is gone" affordance, no "delete the
record" action. It just sits there forever, indistinguishable from a
fresh row except for the status pill. There's also no "Re-provision from
this spec" button, which is the obvious next operator move.

## Lifecycle buttons depend on state and there's no clue what each one does

Pending shows `Provision` / `Terminate`. Running presumably shows `Start`
/ `Stop` / `Restart` / `Terminate`. The buttons have no descriptions and
no danger styling on Terminate. Terminate especially should require
typing the VM ID to confirm.

## No "SSH to this VM" affordance

The IPv6 is the whole point of the system per spec — the operator should
be one click from a copy-to-clipboard `ssh root@[2400:6180:…]` or even
an inline web terminal stub.

## Creation form is too generic

- Description (optional, free text) is the first field — but it's also
  the *only* identifier the operator will see in the list view. Forget
  to fill it and you get a UUID.
- The SSH Public Key should default from the server provider's key (or
  have a "use my account key" shortcut) instead of forcing a paste every
  time.
- Resources (vCPU/Memory/Disk) default to 1 / 512MB / 4GB. No
  small/medium/large preset, no hint of "what the bootstrapped Ubuntu
  image actually needs". The operator has to guess.
- No cost preview. Not even an estimate of "this VM will use X of Y
  available cores on the server".
