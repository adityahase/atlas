# UI audit — Atlas vs the Frappe UI app family

A token-level comparison of Atlas against Frappe CRM, Gameplan,
ERPNext Desk, and the canonical Frappe UI component library. This
file is the source-of-truth audit that motivates every rule in
[`atlas/public/css/atlas_desk.css`](../atlas/public/css/atlas_desk.css).

Method: Playwright-driven side-by-side captures of each surface,
computed-style reads on the key elements, and cross-checks against
`llm/references/frappe-ui/tailwind/tokens.js` and
`llm/references/frappe-ui/tailwind/plugin.js`.

## Headline finding

**Atlas already uses the same design tokens as ERPNext, CRM, and
Gameplan.** Same `InterVar` font, same `--ink-gray-*` and
`--surface-gray-*` palette, same `8px` radius, same `14px` body
text, same `420` body weight, same `0.02em` letter-spacing. At the
token layer there is nothing to "modernize."

The visible gap between Atlas and the modern apps is **page
architecture, not styling.** Atlas inherits Frappe Desk's chrome
(page-head with breadcrumbs + Actions menu + Save, sections of
fields, right rail, bottom timeline); CRM and Gameplan are Vue SPAs
built directly on `frappe-ui`, so they use a tighter top bar, big
tabs, big primary actions, and content-first detail pages dominated
by an activity stream.

So the audit splits into two buckets:

1. **What cannot change without rewriting Atlas as a Vue SPA** —
   page chrome, list architecture, form-as-activity-stream. Out of
   scope; explicitly *not* attempted.
2. **What can change inside Atlas's current Desk-based UI** —
   workspace layout, list-view cell renderers, custom buttons via
   `atlas_form_overrides.js`, dashboard cards, scoped CSS. This is
   where every shipped fix lives.

## Tokens that already match

Verified by computed-style read on a live Atlas page vs
`tokens.js`:

| | Value |
| --- | --- |
| Font family | `InterVariable` |
| Body size / weight / line-height | `14px` / `420` / `1.5` (Desk inherits Bootstrap default) |
| Letter-spacing (body) | `0.02em` |
| Primary text (`--ink-gray-9`) | `#171717` |
| Body text (`--ink-gray-8`) | `#383838` |
| Muted text (`--ink-gray-5`) | `#7c7c7c` |
| Surface bg / input bg | `#ffffff` / `#f3f3f3` (`--surface-gray-2`) |
| Border default (`--outline-gray-1`) | `#ededed` |
| Input / button radius | `8px` |
| Primary button bg | `#171717` (Solid) |
| Subtle button bg / text | `#f3f3f3` / `#383838` (matches frappe-ui Subtle exactly) |

Atlas's CSS variables are **byte-identical** to ERPNext's and
Gameplan's. No token-layer change is required or beneficial.

Theme variants — informational pills inside the button shape — are
also the same family:

| Theme | Bg | Text |
| --- | --- | --- |
| Blue | `--surface-blue-2` `#e6f3ff` | `--ink-blue-3` `#007be0` |
| Green | `--surface-green-2` `#e4fae9` | `--green-800` `#075e35` |
| Red | `--surface-red-2` `#ffe7e7` | `--red-700` `#b52a2a` |

These are the tonal values Atlas applies to dropdown rows (red on
destructive Actions, green on safe-but-primary Actions) — see
spec §"Tonal dropdown items".

## Where Atlas drifted from the family (and the fix)

Each drift below maps to a single rule (or small group of rules) in
`atlas_desk.css`. The spec documents the rule in §"Visual polish";
this section is the evidence that motivated it.

1. **Sidebar items run edge-to-edge with no hover radius.** Frappe
   Desk's `body-sidebar` paints `.standard-sidebar-item` as a
   square-cornered full-bleed row; the frappe-ui `<Sidebar>` used
   by CRM and Gameplan gives every item an 8px horizontal inset and
   an 8px-radius hover/active fill. This is the **single most
   visible "Atlas looks like Desk" tell.**
2. **Form labels at `ink-gray-7` (`#525252`)** — only marginally
   lighter than the value inside the input, so the eye has to
   decode "label" vs "value." CRM and frappe-ui's `FormControl`
   paint labels `ink-gray-5` (`#7c7c7c`), clearly muted.
3. **No tab structure on heavy forms.** Server, VM, VM Image, and
   Task each carried 4–6 vertical sections, all stacked.
4. **`text-danger` rows in Actions menus lack a tonal fill on
   hover.** The text was red but the hover bg was the default grey,
   so destructive rows didn't pop. CRM-style tonal rows fill with
   `--surface-red-2` on hover.
5. **Empty list view rendered top-left-aligned with no breathing
   room.** Frappe ships an icon + message + Create CTA in
   `.no-result`; only the layout was off.
6. **Two solid-black buttons in the same page head.** Desk paints
   its `Save` as `btn-primary`; an Atlas lifecycle hero
   (`Bootstrap`, `Provision`, `Sync to Server`, `Re-provision as
   new`) also paints as `btn-primary`. Result: two heroes
   competing, breaking the "one primary per page" rule from
   [`llm/Taste.md`](../llm/Taste.md).
7. **Bare reqd-asterisk emitted above some column tops** in current
   Frappe versions, duplicating the asterisk next to the field
   label.
8. **Task `stdout` / `stderr` Code-field panes too short** for
   any non-trivial run.

Everything above is fixed in `atlas_desk.css` + small
`atlas_form_overrides.js` helpers. The spec describes each fix at
the call site; this audit is the *why*.

## What was deliberately not changed

These are valid observations from the audit that we chose not to
action — recorded so a future round doesn't re-open them.

- **Page-head density.** Atlas's page-head (breadcrumbs + prev/next
  + Actions + Save) is busier than CRM's single-row header. Useful
  for power users; not changing.
- **Field-vs-activity page architecture.** CRM's detail page is
  activity-first; Atlas's is field-first. This is appropriate for
  infrastructure records that operators read to *act* on, not
  *comment* on. The spec explains why (§"Why deviate from Frappe
  defaults at all" and §"Why this isn't a custom SPA").
- **List row avatar/initial chip prefix.** CRM Leads shows a
  colored initial-circle avatar before the name. Atlas lists
  already render `get_indicator` status pills in that slot —
  adding a second visual prefix would compete for the same
  scanning slot. See spec §"Per-doctype consequences" for each
  list's indicator + formatter shape.
- **List-view `get_empty_state` per-doctype illustrations.** Frappe
  supports custom empty-state illustrations via a controller hook;
  we did not wire them because the Module Onboarding widget on the
  workspace already greets first-run operators with a guided
  checklist. Layout-only polish on `.no-result` was enough.
- **App icon colour** stays Frappe-default slate-gray
  (`#7b808a`). Branding choice; needs product input, not a UX bug.
- **Connections counts** (linked-record counters on the form) are
  configured wherever a parent→child counter is meaningful: Server
  Provider → Server, Server → Virtual Machine + Task, Virtual
  Machine → Task, Virtual Machine Image → Virtual Machine. Task is
  a leaf with no children that warrant a counter, so it ships no
  `_dashboard.py`.

If any of these become operationally important, file fresh against
the current spec.

## Items that are upstream Frappe concerns

Audit flagged these; they're not Atlas's to fix:

- Ghost `...` widget subtitle on number cards.
- Apps-sidebar icon defaults.
- List filter persistence across workspace-tile clicks.

Captured here so they're not re-discovered as "Atlas bugs."

## Reference data

The Playwright captures (PNGs) and computed-style reads
(`_<app>-*.json`) used to back every claim above were retained
during the audit period and deleted with the per-finding research
files once the spec absorbed the rules. Re-running the audit means
re-running Playwright against the same five surfaces — Atlas Desk,
Gameplan, Frappe CRM, ERPNext Desk, and the Frappe UI docs — and
diffing computed styles against `llm/references/frappe-ui/`.
