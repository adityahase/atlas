import os

import frappe
from frappe.modules.import_file import import_file_by_path


def execute():
	if not frappe.db.table_exists("Workspace Sidebar"):
		return

	# Import the Atlas Workspace Sidebar fixture.
	path = os.path.join(
		frappe.get_app_path("atlas"), "workspace_sidebar", "atlas.json"
	)
	if os.path.exists(path):
		import_file_by_path(path, force=True)
		# Frappe's auto_generate_sidebar_from_module skips modules whose
		# Workspace Sidebar row has for_user IS NULL. import_file_by_path
		# stores the missing field as "" — coerce to NULL so our row wins.
		frappe.db.sql(
			"UPDATE `tabWorkspace Sidebar` SET for_user = NULL "
			"WHERE name = 'Atlas' AND (for_user = '' OR for_user IS NULL)"
		)

	# Generate the Desktop Icon for the /apps launcher tile. Frappe runs this
	# via `after_app_install` only on first install — sites where Atlas was
	# installed before `add_to_apps_screen` was added (e.g. dev environments)
	# need this one-time backfill.
	if not frappe.db.exists("Desktop Icon", {"app": "atlas", "icon_type": "App"}):
		from frappe.utils.install import auto_generate_icons_and_sidebar
		auto_generate_icons_and_sidebar()
