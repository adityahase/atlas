# Workspace — desk landing and Atlas home

Screenshots:
[desk-01-landing.png](./screenshots/desk-01-landing.png),
[desk-02-atlas-workspace.png](./screenshots/desk-02-atlas-workspace.png)

## No empty-state, no zero-to-one guidance

A fresh operator who lands on `/desk` sees an app grid (Atlas, Framework).
Clicking Atlas drops them into a generic workspace with shortcuts to 5
DocTypes — nothing tells them the *required order* (Provider → Server →
Image → VM). The spec says it explicitly; the UI doesn't.

## Two windows now

Clicking the Atlas app icon on `/desk` opens the workspace in a **new
tab**, splitting the session in two for no reason. Most operators won't
expect that.

## The workspace is a Frappe workspace, not an Atlas dashboard

"Active Servers: 1", "Running VMs: 0", "Tasks Today: 9", "Failed Tasks
(24h): 0" are nice — but clicking them goes to a vanilla list view.
There's no:

- "fleet at a glance" panel
- per-server VM count
- recent activity feed
- "you have 3 stuck Pending VMs" callout

The dashboard tells you nothing actionable.

## Shortcut cards duplicate the sidebar

The Workspace shortcut cards (`Server`, `Virtual Machine`, `Task`,
`Virtual Machine Image`) are the same links that already exist in the
sidebar. Wasted vertical space on what is arguably the most important
screen to make useful.
