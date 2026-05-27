# Where to start

The three changes with the biggest UX-per-LOC ratio.

## 1. Replace the Run Task dialog with a script-aware form

- Hide all VM-lifecycle scripts (`provision-vm.sh`, `start-vm.sh`,
  `stop-vm.sh`, `terminate-vm.sh`, `vm-network-up.sh`,
  `vm-network-down.sh`) — they belong on the VM doctype, not here.
- Show only `bootstrap-server.sh`, `reboot-server.sh`, `sync-image.sh`.
- Render per-script field forms instead of raw JSON.

See [03-server.md](./03-server.md) §"Run Task dialog is the worst
offender".

## 2. Make Task detail useful

- Big monospace log panel with auto-tail while running.
- `Retry` button on failure.
- Surface the related VM as a clickable header chip.
- Show the script source that was actually uploaded.

See [06-task.md](./06-task.md).

## 3. Confirm and preview every destructive/expensive action

- Provision Server — cost preview ("this creates a 4 GB DigitalOcean
  droplet in blr1 at $24/mo").
- Reboot — VM count ("this server is running N virtual machines").
- Terminate — typed confirm of the VM ID.
- Sync to All Servers — count preview ("this will sync to N servers").

See [02-server-provider.md](./02-server-provider.md),
[03-server.md](./03-server.md),
[04-virtual-machine-image.md](./04-virtual-machine-image.md),
[05-virtual-machine.md](./05-virtual-machine.md).
