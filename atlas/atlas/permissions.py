"""Row-level access for the Atlas User audience (the dashboard SPA).

Atlas has two audiences (see spec/11-user-ui.md):

- **Operators** are System Manager. They see the whole fleet. Every function
  here short-circuits to "no restriction" for them.
- **Users** hold the `Atlas User` role. They see only the Virtual Machines,
  Snapshots, and Sites they own (Frappe's built-in `owner`), and — for the
  inline Activity panel — only the Tasks of a machine they own. They never see
  Provider, Server, or Task as a navigable surface.

Two halves, both wired in hooks.py:

- `permission_query_conditions` scopes list views / `get_list` (and therefore
  every `useList` the SPA makes).
- `has_permission` guards single-document reads (`get_doc`, opening a row by
  name) for Task, where ownership is indirect (a Task is owned by the system
  user, not the requester, so `if_owner` can't express "tasks of my VM").
"""

import frappe

OPERATOR_ROLE = "System Manager"

# DocTypes whose rows a user owns directly via Frappe's `owner` column. A
# `Site Request` is created by a Guest and re-owned to the verified user at
# fulfilment (spec/14-self-serve.md), so a user can see only their own requests.
_OWNED_DOCTYPES = (
	"Virtual Machine",
	"Virtual Machine Snapshot",
	"SSH Key",
	"Site",
	"Site Request",
	"VPN Tunnel",
	"Firewall",
)


def _is_operator(user: str) -> bool:
	return OPERATOR_ROLE in frappe.get_roles(user)


def owner_only(user: str | None = None, doctype: str | None = None) -> str:
	"""Restrict an owned doctype's list to the current user's own rows.

	Wired for Virtual Machine, Virtual Machine Snapshot, and SSH Key. Operators
	are unrestricted (empty string). The doctype is supplied by Frappe so one
	function serves all — see frappe/database/query.py
	get_permission_query_conditions."""
	user = user or frappe.session.user
	if _is_operator(user):
		return ""
	if doctype not in _OWNED_DOCTYPES:
		# Defensive: only ever expected to run for the two owned doctypes.
		return ""
	return f"`tab{doctype}`.`owner` = {frappe.db.escape(user)}"


def task_by_owned_vm(user: str | None = None, doctype: str | None = None) -> str:
	"""Restrict the Task list to Tasks whose Virtual Machine the user owns.

	A user has no Task surface in the SPA; the only place Tasks appear is the
	inline Activity panel on a machine the user already owns. This keeps a
	hand-rolled `get_list('Task')` from leaking the fleet's task log."""
	user = user or frappe.session.user
	if _is_operator(user):
		return ""
	owned = frappe.db.escape(user)
	return f"`tabTask`.`virtual_machine` in (select `name` from `tabVirtual Machine` where `owner` = {owned})"


def task_has_permission(doc, ptype=None, user=None, **kwargs) -> bool:
	"""Single-document guard for Task: a user may access a Task only if they
	own its linked Virtual Machine.

	Returns True to allow, False to deny. Frappe treats a falsy controller
	result as a hard deny (frappe/permissions.py: `if not controller_permission:
	return bool(...)`), so operators must return True explicitly — not None —
	to keep their standard System Manager Task perms. The query condition
	above scopes lists; this guards opening a Task by name."""
	user = user or frappe.session.user
	if _is_operator(user):
		return True
	if not getattr(doc, "virtual_machine", None):
		return False
	owner = frappe.db.get_value("Virtual Machine", doc.virtual_machine, "owner")
	return owner == user
