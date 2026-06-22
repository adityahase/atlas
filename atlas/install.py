"""App install / migrate hooks.

Wired in hooks.py. `after_migrate` runs on every `bench migrate` (including the
fresh-install migrate), so it is the idempotent place to seed data the app needs but
can't ship as a static fixture — here, the brand denylist (spec/18 Component H), whose
rows are operator-curated after install and so must NOT be overwritten by a fixture
re-sync on every migrate.
"""

import frappe


def after_migrate() -> None:
	"""Idempotently seed the brand/keyword denylist (spec/18 Component H).

	`seed_denylist` only inserts labels not already present, so an operator's edits
	(added rows, a row disabled to lift a block) survive every migrate — unlike a
	Frappe fixture, which would re-assert the seed and clobber operator state. Runs on
	the fresh-install migrate too, so a brand-new site starts with the obvious
	payment/auth brands blocked."""
	from atlas.atlas.doctype.subdomain_denylist.subdomain_denylist import seed_denylist

	# The DocType may not exist yet on the very first migrate of an older site mid-sync;
	# guard so a partial migrate never aborts on the seed.
	if not frappe.db.table_exists("Subdomain Denylist"):
		return
	inserted = seed_denylist()
	if inserted:
		print(f"[atlas] seeded {inserted} Subdomain Denylist row(s)", flush=True)
