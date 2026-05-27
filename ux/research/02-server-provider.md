# Server Provider

Screenshots:
[desk-03-server-provider-list.png](./screenshots/desk-03-server-provider-list.png),
[desk-04-server-provider-form.png](./screenshots/desk-04-server-provider-form.png),
[desk-05-provision-server-dialog.png](./screenshots/desk-05-provision-server-dialog.png)

## `Test Connection` and `Provision Server` look the same

Both are top-bar buttons of equal weight. Test is read-only; Provision
spends real money. They render identically. Provision should be the
primary/coloured action and Test should sit beside it as a quiet link.

## The Provision dialog is one field: `Server Name`

No region/size/image override, no preview of "this will create a 4 GB
DigitalOcean droplet in `blr1` at $24/mo", no confirmation step. The
operator clicks Provision and a billable resource appears with zero
ceremony. For a foundational infra tool this is too quiet.

## No "what's coming back" preview

After clicking Provision the dialog just closes. The newly created Server
doesn't open; there's no toast linking to it; the operator has to navigate
to the Server list manually and figure out which one is new.

## `API Token` and `SSH Private Key` are masked but unverifiable

No way to test "is this token still valid?" without clicking Test
Connection and reading the toast. No expiry display.

## Default Region/Size/Image are free-text Data fields

Not Selects backed by DO's catalog. Typos here silently fail at provision
time. The spec calls out this is a thin layer over DO — but the desk lets
you type `blr2` and find out later that it doesn't exist.
