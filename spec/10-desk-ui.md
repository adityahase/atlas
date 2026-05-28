# Desk UI

The desk is Atlas's only UI. We don't ship a custom SPA; we lean on
Frappe's standard form, list, and dialog primitives. But every Atlas
form goes through a small layer of shared client conventions so the
operator sees a consistent action hierarchy and can't fire expensive
or destructive things by accident. This section documents what that
layer is and why it exists.

A second, narrower layer — scoped CSS in
[`atlas/public/css/atlas_desk.css`](../atlas/public/css/atlas_desk.css)
loaded via `app_include_css` — closes the visible gap between Atlas
and the Frappe UI / CRM / Gameplan family without touching Desk's
core CSS. Each block is documented at the call site below
(["Visual polish"](#visual-polish)); the source-of-truth audit
(token-level comparison with the Frappe UI apps, plus the list of
drifts each CSS rule addresses) is in
[`ui/audit.md`](../ui/audit.md).

## Why deviate from Frappe defaults at all

Frappe's stock form chrome — right rail (Assign / Attachments / Tags /
Share / Last Edited By), bottom Comments / Activity panel — is built
for CRM-shaped records that humans read and annotate. Atlas records are
infrastructure: an operator reads them to act, not to comment on them.
The right rail and timeline take ~50% of the screen and contribute
nothing on a Server, VM, or Task form. So we hide them, deliberately
and per-doctype, and document the decision here so a future contributor
doesn't quietly turn them back on.

We also need a button hierarchy: a desk that renders `Save`,
`Provision`, `Terminate`, `Reboot`, `Test Connection`, `Bootstrap` as
identical pills can't communicate "this one is destructive" or "this
one costs money." Frappe supports primary / secondary / danger button
variants and button groups out of the box; we just have to use them
consistently.

## The shared client surface

One file —
[`atlas/public/js/atlas_form_overrides.js`](../atlas/public/js/atlas_form_overrides.js)
— wired via `doctype_js` for the five Atlas doctypes in
[`hooks.py`](../atlas/hooks.py). It defines `frappe.atlas.*` helpers
and applies a cross-doctype `onload` / `refresh` that strips the right
rail and timeline.

### Button-tier convention

| Tier      | Helper                       | When                                                    | Style                              |
| --------- | ---------------------------- | ------------------------------------------------------- | ---------------------------------- |
| Primary   | `frappe.atlas.add_primary`   | The single most likely action on this form/state pair   | Top bar, `btn-primary`             |
| Secondary | `frappe.atlas.add_secondary` | Frequent siblings (Restart alongside Start / Stop)      | Top bar, default                   |
| Hidden    | `frappe.atlas.add_action`    | Rare actions (Re-bootstrap on an Active server)         | Inside the `Actions ▾` group menu  |
| Danger    | `frappe.atlas.add_danger`    | Destructive (Terminate, Reboot, Delete record)          | Inside `Actions ▾`, `btn-danger`   |

Every doctype's `refresh` calls these helpers, never the bare
`frm.add_custom_button`. The convention is the convention; deviations
should be deliberate and have a reason next to them.

#### One primary per page

Desk's own `Save` button (`.standard-actions .primary-action`) is
painted `btn-primary` on every form load, even on a clean record. With
an Atlas lifecycle hero also rendered as `btn-primary`, the page head
ends up with **two solid-black buttons** — breaking the "one primary
button per page" rule from [`llm/Taste.md`](../llm/Taste.md).

[`atlas/public/css/atlas_desk.css`](../atlas/public/css/atlas_desk.css)
fixes this with a scoped `:has()` rule: whenever a custom
`.btn-primary` exists in `.page-actions .custom-actions`, the sibling
`Save` is demoted to a Subtle / Outline variant (white background,
ink-gray-7 text, gray-3 border). Save keeps its click handler and
Ctrl/Cmd+S binding — only the visual weight drops, so the lifecycle
action reads as the page's single hero. On forms with no custom
primary (Active server, idle Task) Save stays solid, correctly
becoming the page's only primary.

### Form-embedded lists reuse the workspace `quick_list` widget

Three forms surface a short list of related records inside a dashboard
section: Server > **Recent Tasks**, Task > **Sibling Tasks**, and
Virtual Machine Image > **Sync Status**. The Atlas workspace also
surfaces one — **Recent activity** — via Frappe's native `quick_list`
block. Rather than carry our own row template and indicator-colour map
on each form, the form lists instantiate the same widget directly:

```js
frappe.widget.make_widget({
  widget_type: "quick_list",
  document_type: "Task",
  label: __("Recent Tasks"),
  quick_list_filter: JSON.stringify([["server", "=", frm.doc.name]]),
  container: $section,
  options: {},
});
```

That gives every form list the workspace's row markup
(`.quick-list-item` with stacked title + relative time, status pill on
the right, hover, chevron), pill colours from
`frappe.get_indicator()` (same as the Task list view), and the
refresh / filter / "View List" affordances — with **no Atlas CSS**.
The widget hardcodes `page_length: 4` and orders by `creation desc`;
both are accepted in exchange for parity with the workspace block.

The Sync Status panel on Virtual Machine Image is the one exception:
its rows are one-per-active-server with a conditional right cell
("Sync now" vs. last-synced timestamp), not a single-doctype list, so
the widget can't render it. The renderer is bespoke but emits the
same `.quick-list-widget-box` / `.quick-list-item` markup, so it
inherits the same Frappe CSS without an Atlas stylesheet.

### Confirmation helpers

```text
frappe.atlas.confirm_cost({title, body_html, proceed_label, proceed})
frappe.atlas.confirm_destructive({title, body_html, match_string,
                                  match_label, proceed_label, proceed})
```

`confirm_cost` wraps `frappe.warn` with the orange Provision-style
indicator. Used for actions that are not destructive but spend real
money or bandwidth: Provision Server (creates a billable droplet).
Sync to All Servers uses a dedicated `MultiCheck` dialog instead so
the operator can pick the subset of servers to sync to — see
[Virtual Machine Image](#virtual-machine-image).

`confirm_destructive` is a custom dialog with a text-match input. The
red primary button stays disabled until what the operator types
matches `match_string` exactly. Used for: Reboot a server (match the
server name), Terminate a VM (match the VM's 8-char short ID), Delete
a Terminated VM record.

The match-string pattern is the same one GitHub uses for "delete
repository": the operator can't muscle-memory through it.

### Toast-and-route after every Task spawn

```text
frappe.atlas.task_started(frm, label, task_name)
```

Every controller method that returns a new Task name routes the
operator to the Task form and drops a blue toast on the source form
linking back. Latency hint copy lives inside each action's dialog
(`~90 s` for Provision Server, `~5 s` for Start, etc.) so the operator
knows what's normal.

### Chrome strip

`frappe.atlas.strip_desk_chrome(frm)`, attached to `onload` and
`refresh` for the five Atlas doctypes, hides:

- `frm.page.sidebar` — the right rail (Assign, Tags, Share, …).
- `.new-timeline` and `.comment-input-container` inside
  `frm.page.wrapper` — the activity panel and comment box.

The main column then expands from `col-lg-8` to `col-lg-12` so the
form breathes. We hide DOM nodes; we don't monkeypatch Frappe globals.

Connections dashboards (the count tiles for Workloads, Tasks, …) stay
visible — those *are* useful and Frappe renders them on the form
itself, not in the right rail.

## The workspace

The Atlas workspace is the operator's home. It is restructured around three
sections, top-to-bottom:

1. **Bootstrap checklist** — Frappe's native `Module Onboarding` widget,
   wired into the workspace `content` as a `type: "onboarding"` block.
   The four steps (Add Server Provider → Provision Server → Add Virtual
   Machine Image → Provision Virtual Machine) ship as
   [`module_onboarding/atlas_setup/`](../atlas/atlas/module_onboarding/atlas_setup/)
   plus four
   [`onboarding_step/<slug>/`](../atlas/atlas/onboarding_step/)
   JSON files. Each step's `reference_document` points at the target
   DocType; the operator clicks the step, lands on the create form, and
   on save the widget flips `is_complete` for that step. When all four
   are satisfied the widget collapses itself and can be permanently
   dismissed — no Atlas code, no fixture HTML/CSS/JS. The earlier
   custom-HTML implementation (`atlas-bootstrap-checklist`,
   `bootstrap_status()`) is gone.
2. **Fleet at a glance** — four `number_card` blocks: Active Servers,
   Running Virtual Machines, Pending Virtual Machines (tinted amber to
   draw the eye when stuck), Failed Tasks (24h) (tinted red). Frappe's
   Number Card doesn't support threshold-driven colour, so the tint is
   static; visual weight still scales with the count.
3. **Recent activity** — a `quick_list` block bound to Task. The last
   ten Task rows with their status, subject, and relative time, so the
   operator sees what the fleet is doing without leaving the workspace.

The workspace deliberately drops the "Your Shortcuts" row and the
"Reports & Masters" card section that earlier duplicated the sidebar.
The sidebar still carries Home and the five doctype links — that *is*
the right primitive for navigation, so the workspace doesn't repeat it.

The multi-app launcher (`/desk`, `/app/home`) is *not* hidden: Frappe
short-circuits `/desk` rendering before `website_redirects` can fire
([`apps/frappe/frappe/website/path_resolver.py:34`](../../frappe/frappe/website/path_resolver.py#L34)),
so we accept a one-click cost to enter Atlas from a fresh login.
Bookmarks and the sidebar Home button hit `/app/atlas` directly.

## Visual polish

[`atlas/public/css/atlas_desk.css`](../atlas/public/css/atlas_desk.css)
is the *only* CSS Atlas adds to Desk. Every rule below was justified by
a side-by-side comparison with Frappe CRM, Gameplan, and the canonical
Frappe UI components (see [`ui/audit.md`](../ui/audit.md)). The file
is small and scoped — each block opens with a comment that points back
to the audit finding that motivated it.

### Sidebar items — inset and rounded

Desk's stock sidebar items run edge-to-edge with no hover radius. The
Frappe UI `<Sidebar>` (used by CRM and Gameplan) gives every item an
8px horizontal inset and an 8px-radius hover/active fill. Atlas
applies the same shape to `.body-sidebar .standard-sidebar-item`
(and the nested `.sidebar-child-item`). Active items pick up
`--surface-gray-3`; hover lands on `--surface-gray-2`.

Frappe marks the current workspace with `.active-sidebar` (not
`.selected`, which an older spec assumed) — the selector in the CSS
file matches the live DOM. The inner `.item-anchor` is forced
transparent so the radius can clip the fill cleanly.

### Form field labels — softened to ink-gray-5

Desk's `.control-label` defaults to `--ink-gray-7` — only marginally
lighter than the value inside the input, so the eye has to decode
"label" vs "value." Frappe UI's `FormControl` paints labels
`--ink-gray-5`, clearly muted. Atlas applies the same one-line
override (`.frappe-control .control-label { color: var(--ink-gray-5); }`)
so values read louder than their labels. Section headers, modal
titles, and dialog labels are untouched — the rule is scoped to
`.frappe-control`.

### Tab Break separators on heavy forms

Server, Virtual Machine, Virtual Machine Image, and Task each carried
4–6 vertical sections; the form was scroll-heavy. The doctype JSON
files now group those sections into tabs via `fieldtype: "Tab Break"`:

| Doctype | Tabs |
| --- | --- |
| Server                | Overview · Networking · Host info |
| Virtual Machine       | Overview · Networking · Activity |
| Virtual Machine Image | Overview · Image data |
| Task                  | Overview · Output |

No fields moved between sections; only the section/tab boundary
changed. Dashboard panels (Operations, Recent Tasks, headlines)
render *above* the tab strip and remain visible across all tabs.

### Tonal dropdown items — red and green

`frappe.atlas.add_danger` already paints destructive Actions-menu rows
with `text-danger` (red text). The CSS now also paints the whole row
`--surface-red-2` on hover, matching the frappe-ui Button
`theme=red, variant=subtle` look. A sibling helper
`frappe.atlas.add_success` does the same in green
(`--surface-green-2` on hover, `--green-800` text) for safe-but-primary
items that fold into Actions on a non-default state (e.g.
`Re-bootstrap` on an Active server).

### List empty-state polish

A filtered list with zero matches rendered top-left aligned with no
breathing room. The CSS centers `.list-view .no-result`, caps it at
420px, gives it 48px of vertical padding, and pushes the "Create a
new …" button below the message. Frappe already ships the icon and
the CTA — Atlas only adjusts the layout, no controller method needed.

### One primary per page — Save demotion

Documented above under [Button-tier convention](#button-tier-convention).
The same CSS file owns the `:has()` rule that demotes Desk's `Save`
to outline whenever an Atlas custom `.btn-primary` exists in the page
head, so the lifecycle action reads as the page's single hero.

### Log panes — taller stdout / stderr on Task

`Task.stdout` and `Task.stderr` are `Code` fields. Desk's default pane
height makes any non-trivial run a scroll-inside-a-textarea exercise.
A scoped CSS rule sets `min-height: 24em` on
`.frappe-control[data-fieldname="stdout"|"stderr"] textarea, .CodeMirror`,
which catches both the plain textarea and the CodeMirror wrapper
(Desk swaps between them depending on the Code field's `options`).
The earlier JS-side `enlarge_log_panes` helper is gone.

### Orphan reqd-asterisk

Some Frappe versions emit a bare `*` element above a column whose
first field is required — duplicating the asterisk already rendered
next to the label. A one-line rule
(`.form-column .section-body > .reqd:not(.frappe-control) { display: none }`)
hides the wrapper case. The text-node case (no element to target)
is still stripped by `suppress_orphan_asterisks` in
`atlas_form_overrides.js` — CSS can't select text nodes.

## Per-doctype consequences

### Server Provider

- **Provision Server** is the primary action.
- **Test Connection** lives under `Actions ▾`. It's a cheap read-only
  ping; it doesn't need top-bar real estate.
- The Provision dialog shows a defaults preview block (region, size,
  monthly USD cost, image) above the Server Name field, then hands
  off to `confirm_cost` ("Create a billable droplet?"). Cost comes
  from a hand-maintained `DIGITALOCEAN_MONTHLY_COST_USD` dict — same
  policy as `default_image` (DO doesn't expose pricing per size in
  their API). Missing sizes render as "—" rather than guess.
- A **credential indicator** auto-runs on form refresh for DigitalOcean
  providers. `Server Provider.credential_check` hits the DO `/account`
  endpoint and returns `{ok, email, rate_limit, rate_remaining}` or
  `{ok: false, error}`; the client paints a green
  `✓ API token valid (4999/5000)` or a red `✗ API token invalid` chip
  via `frm.dashboard.add_indicator`. Result is cached for five minutes
  in `frm._atlas_credential_cache`; the **Test Connection** action
  invalidates the cache so the operator can re-verify on demand. Test
  Connection also fires a blue `Testing connection…` toast
  immediately on click so the operator knows the click landed before
  the network round-trip resolves.

### Server

- **Bootstrap** is primary when the server is `Pending` /
  `Bootstrapping` / `Broken`. On an Active server it folds under
  `Actions ▾` as **Re-bootstrap** — re-bootstrapping a healthy host
  is rare enough not to compete for top-bar real estate.
- **Run Task** and **Reboot** always live under `Actions ▾`. The Run
  Task dialog is 100% server-driven: `Server.get_scripts()` returns
  `[{name, intro, fields}, ...]` straight out of
  `scripts_catalog.SCRIPT_FORMS`, and the client passes each entry's
  `fields` to `frappe.ui.Dialog` after gating them with `depends_on:
  'eval:doc.script === "<name>"'`. No script schema lives client-side
  — adding a new operator-visible script means adding one entry to
  `SCRIPT_FORMS`, nothing else.
- **Reboot** is danger. It demands the operator type the server name
  in a `confirm_destructive` dialog that also shows the running-VM
  count.
- A yellow **headline alert** announces any Pending/Running Task on
  this server, linking to the Task form. The alert refreshes on the
  `task_update` realtime event.
- A **Recent Tasks** dashboard section lists recent Tasks for this
  server. Rendered by Frappe's native `quick_list` widget (see
  [Form-embedded lists reuse the workspace `quick_list` widget](#form-embedded-lists-reuse-the-workspace-quick_list-widget)),
  so the row markup, pill colours, and "View List" footer match the
  workspace **Recent activity** block. The `task_update` realtime hook
  on this form tears down and recreates the widget so the list stays
  current as Tasks transition.

### Virtual Machine

- Lifecycle buttons follow a status-keyed hierarchy:
  - `Pending` / `Failed` → **Provision** primary.
  - `Stopped` → **Start** primary, **Restart** secondary.
  - `Running` → **Stop** primary, **Restart** secondary.
  - `Terminated` → no lifecycle buttons; instead **Re-provision as
    new** is primary and **Delete record** is danger (under
    `Actions ▾`).
- **Terminate** is always available (until status = Terminated),
  under `Actions ▾`, danger. The `confirm_destructive` dialog shows
  IPv6, image, server, and demands the operator type the VM's 8-char
  short ID.
- The form header carries an `IPv6 [...]` indicator chip painted via
  `frm.dashboard.add_indicator` (green when Running, orange when
  Pending, red when Failed, grey otherwise). The Networking section
  auto-expands while the VM is `Pending` so the address is visible
  before Provision.
- The Access section carries an `ssh_command` field — a `Code` field
  with `is_virtual: 1` + `read_only: 1`, value computed by an
  `@property ssh_command` on the VM controller (`ssh root@<ipv6>`).
  Frappe's read-only Code control paints its own copy button, so we
  ship no markup of our own. The IPv6 is the only stable identifier
  outside the desk.
- **Terminated** records render a red dashboard headline
  (`⛔ Terminated <when>. This record is kept for audit; the VM no
  longer exists.`); the **Re-provision as new** button opens a new VM
  form with the same server / image / vcpus / memory / disk / ssh key
  and a `(clone)`-suffixed description pre-filled.
- The list view shows `<description> · <short id>` in the subject
  column, an IPv6 copy chip, and status-coloured indicators
  (`Pending` orange, `Running` green, `Stopped`/`Terminated` grey,
  `Failed` red).
- When the linked provision Task ends in `Failure`, the
  Task.on_update hook flips the VM's `status` from `Pending`/`Running`
  to `Failed` via `frappe.db.set_value` and publishes a
  `virtual_machine_update` realtime event. The VM form subscribes and
  reloads. For `Pending`/`Failed` VMs the client also renders a red
  intro that links to the most recent provision-vm.sh Failure Task —
  the operator clicks the link, reads the error, and clicks Provision
  again to retry.
- The **creation form** (new VM) shows three affordances on top of the
  raw schema: a yellow `Description` nudge until the operator types a
  label; a `size_preset` `Select` field (Custom / Small / Medium /
  Large, each labelled with its `vCPU / MB / GB`) at the top of the
  Resources section that writes all three Int fields in one click via
  a one-line `size_preset(frm)` change handler; and a dashboard
  headline `Server capacity: X requested + Y used / Z total (N VMs)`.
  The headline turns orange at the cap and red — with a `⚠ Server is
  oversubscribed` suffix — when projected use exceeds total. Capacity
  is computed by `atlas.atlas.api.server_capacity.capacity_for_server`,
  backed by a hand-maintained `size → vCPUs` dict (same maintenance
  model as the monthly-cost dict on Server Provider).

### Virtual Machine Image

- **Sync to Server** is the top-bar secondary action. The picker uses
  `only_select: 1` (no "+ Create a new Server" affordance) and a
  `status = Active` filter — syncing to a Pending/Bootstrapping server
  is wrong because the bootstrap installs Firecracker and the sync
  target directory.
- **Sync to All Servers** lives under `Actions ▾`. Before fanning
  out it opens a `frappe.ui.Dialog` with a `MultiCheck` field
  pre-populated with every Active server (all checked); the operator
  can deselect any they didn't intend to sync to. The dialog primary
  posts the selected list to `sync_to_all_servers(servers=[...])`. The
  controller falls back to "every Active server" when called with no
  list, so non-desk callers (bootstrap, e2e) keep the old shape.
- A **Sync Status** panel at the top of the form lists each Active
  server with the last successful `sync-image.sh` Task for this image
  (`<when ago>` and a green **Synced** pill — clicking the row opens
  the Task). Servers never synced show a grey **Never** pill plus a
  **Sync now →** link that opens the Sync to Server dialog with the
  server pre-filled. The panel emits `.quick-list-widget-box` /
  `.quick-list-item` markup — same as the `quick_list` widget the
  Server and Task lists use (see [Form-embedded lists reuse the
  workspace `quick_list` widget](#form-embedded-lists-reuse-the-workspace-quick_list-widget))
  — but the rows are computed by the controller's `sync_status()`
  method rather than `quick_list` itself, because one row per active
  server with conditional right-cell logic doesn't fit the widget's
  single-doctype query model.
- Once any successful sync exists for an image, the kernel and rootfs
  fields (`kernel_url`, `kernel_filename`, `kernel_sha256`,
  `rootfs_url`, `rootfs_filename`, `rootfs_sha256`) are **locked**.
  Server-side `validate` throws on any change; the client mirrors the
  lock via `read_only` and shows a blue intro: "This image has been
  synced. To change kernel or rootfs, create a new image
  (e.g. `<name>-v2`)." Editing in place would silently invalidate prior
  audit rows that reference a different digest.

### Task

- The form is read-only (`disable_save()`).
- Status-coloured dashboard headline:
  - Pending → blue, "Queued — waiting for worker."
  - Running → yellow, "Running on <server> — started 12s ago."
  - Success → green, "Completed in 28s. Exit code 0."
  - Failure → red, "Failed in 16s. Exit code 1." + the first
    non-trace stderr line as a one-line hint.
- Header chips for the related Server, Virtual Machine, and
  triggered-by User. VM is shown by description, not UUID.
- **Retry** button (primary) when status = Failure. Delegates to the
  matching VM controller method (`provision()`, `start()`,
  `terminate()`, …) for VM-scoped scripts, or to
  `Server.run_task_dialog(...)` for server-scoped scripts. The
  state-machine guards live in those methods — the Retry button does
  not duplicate them.
- **Sibling Tasks** — the most recent other Tasks for the same VM
  (or Server when the Task has no VM) — so the operator can hop
  between Tasks for one workload without navigating through the VM
  form. Rendered by Frappe's `quick_list` widget filtered by
  `virtual_machine` (or `server`) with the current Task excluded; see
  [Form-embedded lists reuse the workspace `quick_list` widget](#form-embedded-lists-reuse-the-workspace-quick_list-widget).
- The `Variables (JSON)` field is **pretty-printed for read**: a
  one-shot client formatter parses `frm.doc.variables` on refresh,
  rewrites it with 2-space indent if and only if the parsed value
  round-trips, and refreshes the field without marking the form
  dirty. The stored value is untouched; only the on-screen render
  changes.
- `Task.on_update` propagates status to linked records. For Failure
  with `script = provision-vm.sh` it flips the linked VM's status to
  `Failed` and publishes a `virtual_machine_update` realtime event —
  the VM form re-renders without manual refresh.

## Why this isn't a custom SPA

Every win above lives in a Frappe `Dialog`, a `Module Onboarding`
widget, a `quick_list` widget, a `MultiCheck` field, a button group,
a form intro, a dashboard indicator, a `doctype_js` client script,
or one small scoped CSS file. We don't replace the Desk form. We
don't add a route. We don't add a build step. The whole thing is
Desk plus ~1.4k lines of shared client JS across the five
doctype scripts + helper module, ~200 lines of scoped CSS
([Visual polish](#visual-polish)), and a handful of whitelisted
controller methods (`preview_cost`, `retry`, `get_scripts`,
`sync_status`, `capacity_for_server`, …).

Anything that *looks* bespoke is borrowed: form-embedded activity
panels use Frappe's `quick_list` widget; the workspace onboarding
checklist is Frappe's `Module Onboarding` doctype; the Run Task
dialog renders from a server-side script catalog
(`scripts_catalog.SCRIPT_FORMS`); the VM size presets are a `Select`
field; the VM SSH command is a virtual `Code` field whose value comes
from a `@property` on the controller; the Sync-to-All targets picker
is a `MultiCheck`. The pattern: if Desk has a primitive for it, we
pass parameters to that primitive — we don't hand-roll markup.

The two places we explicitly fight Desk are documented at the call
site: the chrome strip (right rail + timeline) on every form, and the
Task form's read-only/headline override that suppresses the standard
six-field top row in favor of the dashboard headline + chips. Both
are intentional; both are reversible by removing one client script.
