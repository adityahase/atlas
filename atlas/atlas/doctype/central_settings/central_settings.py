import frappe
from frappe.model.document import Document

from atlas.atlas.central import (
	CentralClient,
	upsert_central_images,
	upsert_central_sizes,
)
from atlas.atlas.secrets import get_secret


class CentralSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Data
		api_secret: DF.Password
		atlas_id: DF.Data | None
		enabled: DF.Check
		last_event_status: DF.SmallText | None
		last_sync: DF.Datetime | None
		region: DF.Data | None
		registered_on: DF.Datetime | None
		url: DF.Data
	# end: auto-generated types

	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Ping Central. Mirrors DigitalOceanSettings.test_connection — returns a
		plain dict the form turns into a toast."""
		result = self.client().ping()
		return {"ok": result.ok, "label": result.label, "error": result.error}

	@frappe.whitelist()
	def register(self) -> dict:
		"""Announce this Atlas to Central by creating its Atlas Instance row there.

		The row carries this Atlas's base_url and an Administrator API key/secret
		so Central can drive this Atlas in return. Central autonames the row by
		region and rejects a duplicate, so registering an already-known region
		fails with a red toast."""
		registration = self.client().register(self._identity(), _mint_callback_credentials)
		self.atlas_id = registration.atlas_id
		self.registered_on = frappe.utils.now_datetime()
		self.save()
		return {"ok": True, "atlas_id": registration.atlas_id, "label": registration.label}

	@frappe.whitelist()
	def fetch_sizes(self) -> dict:
		"""Pull Central's VM size catalog into Central Size rows."""
		summary = upsert_central_sizes(self.client().fetch_sizes())
		self.db_set("last_sync", frappe.utils.now_datetime())
		return summary

	@frappe.whitelist()
	def fetch_images(self) -> dict:
		"""Pull Central's expected bench images into Central Image rows."""
		summary = upsert_central_images(self.client().fetch_images())
		self.db_set("last_sync", frappe.utils.now_datetime())
		return summary

	def client(self) -> CentralClient:
		if not self.url or not self.api_key:
			frappe.throw("Set Central URL and API Key first")
		secret = get_secret("Central Settings", "Central Settings", "api_secret")
		return CentralClient(self.url, self.api_key, secret)

	def _identity(self) -> dict:
		"""How Central reaches this Atlas: its region, base_url, and status. The
		callback credentials are added by register() via _mint_callback_credentials,
		minted only once the region is confirmed free. Field names match Central's
		Atlas Instance DocType."""
		region = self.region or frappe.conf.get("atlas_do_region")
		if not region:
			frappe.throw("Set a Region (or atlas_do_region in site config) before registering")
		return {
			"region": region,
			"base_url": frappe.utils.get_url(),
			"status": "Active",
		}


def _mint_callback_credentials(user: str = "Administrator") -> dict:
	"""Issue the API key/secret Central uses to call back into this Atlas as
	`user`. Mirrors frappe.core.doctype.user.user.generate_keys: reuse the
	existing api_key (or generate one), always issue a fresh api_secret — Frappe
	only stores the secret hashed, so the plaintext must be regenerated each time
	to be transmittable to Central."""
	doc = frappe.get_doc("User", user)
	api_secret = frappe.generate_hash(length=15)
	if not doc.api_key:
		doc.api_key = frappe.generate_hash(length=15)
	doc.api_secret = api_secret
	doc.save(ignore_permissions=True)
	return {"api_key": doc.api_key, "api_secret": api_secret}
