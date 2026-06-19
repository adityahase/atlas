"""Central API client.

Central is the global control plane (spec/16-central.md). One Central manages
many Atlas instances; Atlas is the *client*. This is the inverse of the Provider
relationship — so the client mirrors atlas/atlas/digitalocean.py: a thin
requests wrapper, one *Error type, dataclasses for the typed responses.

Central is an ordinary Frappe site, not a bespoke API. Atlas authenticates to it
as a privileged Central user (an API key/secret stored in Central Settings) and
drives Central's standard document API:

- **ping** — log in with those keys and confirm the session resolves to a real
  user (`frappe.auth.get_logged_user` != Guest), via FrappeClient.
- **register** — `insert` an `Atlas Instance` row on Central (the read-side
  registry Central keeps of every Atlas). The row carries this Atlas's `base_url`
  and a callback `api_key`/`api_secret` (Administrator's, minted Atlas-side) so
  Central can drive this Atlas in return.

Fetch Sizes / Fetch Images / event reporting still call Central's whitelisted
`central.api.*` methods over a token header (see `_request` / `_ROUTES`); those
are untouched.
"""

from __future__ import annotations

import dataclasses

import frappe
import requests
from frappe.frappeclient import (
	AuthError,
	FrappeClient,
	FrappeException,
	SiteExpiredError,
	SiteUnreachableError,
)

DEFAULT_TIMEOUT = 30

# Central's read-registry DocType — one row per Atlas. `register` inserts here.
CENTRAL_ATLAS_DOCTYPE = "Atlas Instance"

# Everything FrappeClient can raise on a failed call. ping/register translate
# these into CentralError so callers see one error type, never a leaked
# transport exception. AuthError/Site* carry no message, so _describe names them.
_CLIENT_ERRORS = (
	FrappeException,
	AuthError,
	SiteExpiredError,
	SiteUnreachableError,
	requests.RequestException,
)


def _describe(exception: Exception) -> str:
	"""Human-readable text for a FrappeClient failure. The auth/reachability
	exceptions are message-less marker classes, so fall back to the class name."""
	return str(exception) or type(exception).__name__


# Central method routes for the telemetry seam (sizes / images / events). Pinned
# in one place — the wire contract from spec/16-central.md § "The wire contract".
# ping / register are NOT here: they go through Central's standard Frappe API
# (login + document insert), not bespoke central.api.* methods.
_ROUTES = {
	"sizes": "central.api.sizes",
	"images": "central.api.images",
	"event": "central.api.event",
}


class CentralError(Exception):
	pass


@dataclasses.dataclass(frozen=True, slots=True)
class CentralAuthResult:
	ok: bool
	label: str | None = None
	error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RegistrationResult:
	atlas_id: str
	label: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CentralSizeInfo:
	slug: str
	title: str
	vcpus: int
	cpu_max_cores: float
	memory_megabytes: int
	disk_gigabytes: int
	monthly_cost_usd: int | None = None
	central_metadata: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CentralImageInfo:
	image_name: str
	title: str
	series: str | None = None
	central_metadata: dict | None = None


class CentralClient:
	"""Talks to a single Central instance. Constructed from Central Settings."""

	def __init__(
		self,
		url: str,
		api_key: str,
		api_secret: str,
		timeout: int = DEFAULT_TIMEOUT,
		verify: bool = True,
	):
		self.url = url.rstrip("/")
		self.api_key = api_key
		self.api_secret = api_secret
		self.timeout = timeout
		self.verify = verify

	def ping(self) -> CentralAuthResult:
		"""Credential check. Logs in to Central with the configured keys and
		confirms the session resolves to a real user. Never raises — returns
		ok=False so the Test Connection toast can render a red indicator."""
		try:
			user = self._frappe_client().get_api("frappe.auth.get_logged_user")
		except _CLIENT_ERRORS as exception:
			return CentralAuthResult(ok=False, error=_describe(exception))
		if not user or user == "Guest":
			return CentralAuthResult(ok=False, error="Not logged in to Central (Guest session)")
		return CentralAuthResult(ok=True, label=user)

	def register(self, identity: dict, mint_credentials) -> RegistrationResult:
		"""Create this Atlas's `Atlas Instance` row on Central. `identity` is the
		region + base_url + status; `mint_credentials` is a zero-arg callable
		returning the {api_key, api_secret} Central will use to call back.

		The callback secret is minted *only after* the region is confirmed free —
		minting rotates a live secret, so a duplicate-region registration must not
		clobber the credentials of the registration already on Central. Central
		autonames by region, so the insert is the hard duplicate guard; the
		pre-check just turns it into a clean message."""
		region = identity.get("region")
		if not region:
			raise CentralError("Cannot register without a region")
		client = self._frappe_client()
		try:
			if client.get_value(CENTRAL_ATLAS_DOCTYPE, "name", {"region": region}):
				raise CentralError(f"Atlas for region {region!r} is already registered on Central")
			doc = client.insert({"doctype": CENTRAL_ATLAS_DOCTYPE, **identity, **mint_credentials()})
		except _CLIENT_ERRORS as exception:
			raise CentralError(_describe(exception)) from exception
		atlas_id = (doc or {}).get("name")
		if not atlas_id:
			raise CentralError("Central did not return the created Atlas Instance")
		return RegistrationResult(atlas_id=atlas_id, label=atlas_id)

	def fetch_sizes(self) -> tuple[CentralSizeInfo, ...]:
		rows = self._request("GET", "sizes").get("sizes", [])
		return tuple(
			CentralSizeInfo(
				slug=row["slug"],
				title=row.get("title") or row["slug"],
				vcpus=int(row.get("vcpus") or 0),
				cpu_max_cores=float(row.get("cpu_max_cores") or 0),
				memory_megabytes=int(row.get("memory_megabytes") or 0),
				disk_gigabytes=int(row.get("disk_gigabytes") or 0),
				monthly_cost_usd=row.get("monthly_cost_usd"),
				central_metadata=row,
			)
			for row in rows
		)

	def fetch_images(self) -> tuple[CentralImageInfo, ...]:
		rows = self._request("GET", "images").get("images", [])
		return tuple(
			CentralImageInfo(
				image_name=row["image_name"],
				title=row.get("title") or row["image_name"],
				series=row.get("series"),
				central_metadata=row,
			)
			for row in rows
		)

	def post_event(self, event: dict) -> None:
		self._request("POST", "event", json=event)

	def _frappe_client(self) -> FrappeClient:
		"""A FrappeClient bound to Central, authenticated by the stored key/secret.

		Construction sets the auth header but does not hit the network — bad keys
		surface on the first call (e.g. ping's get_logged_user), translated to
		CentralError by the calling method."""
		return FrappeClient(self.url, api_key=self.api_key, api_secret=self.api_secret, verify=self.verify)

	def _request(self, method: str, route_key: str, json: dict | None = None) -> dict:
		url = f"{self.url}/api/method/{_ROUTES[route_key]}"
		headers = {
			"Authorization": f"token {self.api_key}:{self.api_secret}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}
		try:
			response = requests.request(method, url, json=json, headers=headers, timeout=self.timeout)
		except requests.RequestException as exception:
			raise CentralError(f"{method} {route_key}: {exception}") from exception
		if response.status_code >= 400:
			raise CentralError(f"{method} {route_key} -> {response.status_code}: {response.text}")
		if not response.content:
			return {}
		body = response.json()
		# Frappe wraps whitelisted return values in {"message": ...}. Unwrap so
		# callers see Central's payload directly, but tolerate a bare object too.
		if isinstance(body, dict) and "message" in body:
			message = body["message"]
			return message if isinstance(message, dict) else {"message": message}
		return body


# --- Local catalog upserts -------------------------------------------------
# Mirror atlas/atlas/doctype/provider/provider.py upsert_catalog: insert or
# update each fetched row, then disable rows Central no longer lists.


def upsert_central_sizes(sizes: tuple[CentralSizeInfo, ...]) -> dict:
	inserted = updated = 0
	seen: set[str] = set()
	for size in sizes:
		seen.add(size.slug)
		values = {
			"title": size.title,
			"vcpus": size.vcpus,
			"cpu_max_cores": size.cpu_max_cores,
			"memory_megabytes": size.memory_megabytes,
			"disk_gigabytes": size.disk_gigabytes,
			"monthly_cost_usd": size.monthly_cost_usd,
			"central_metadata": frappe.as_json(size.central_metadata or {}),
			"enabled": 1,
		}
		if frappe.db.exists("Central Size", size.slug):
			frappe.db.set_value("Central Size", size.slug, values)
			updated += 1
		else:
			frappe.get_doc({"doctype": "Central Size", "slug": size.slug, **values}).insert(
				ignore_permissions=True
			)
			inserted += 1
	disabled = _disable_missing("Central Size", seen)
	return {"inserted": inserted, "updated": updated, "disabled": disabled}


def upsert_central_images(images: tuple[CentralImageInfo, ...]) -> dict:
	inserted = updated = 0
	seen: set[str] = set()
	for image in images:
		seen.add(image.image_name)
		local_image = (
			image.image_name if frappe.db.exists("Virtual Machine Image", image.image_name) else None
		)
		values = {
			"title": image.title,
			"series": image.series,
			"central_metadata": frappe.as_json(image.central_metadata or {}),
			"local_image": local_image,
			"bake_status": _bake_status(local_image),
			"enabled": 1,
		}
		if frappe.db.exists("Central Image", image.image_name):
			frappe.db.set_value("Central Image", image.image_name, values)
			updated += 1
		else:
			frappe.get_doc({"doctype": "Central Image", "image_name": image.image_name, **values}).insert(
				ignore_permissions=True
			)
			inserted += 1
	disabled = _disable_missing("Central Image", seen)
	return {"inserted": inserted, "updated": updated, "disabled": disabled}


def _bake_status(local_image: str | None) -> str:
	"""Expected (nothing baked) vs Baked (a matching active image exists) vs
	Stale (a row exists but is no longer active)."""
	if not local_image:
		return "Expected"
	is_active = frappe.db.get_value("Virtual Machine Image", local_image, "is_active")
	return "Baked" if is_active else "Stale"


def _disable_missing(doctype: str, seen: set[str]) -> int:
	"""Set enabled=0 on rows Central no longer lists. Mirrors the disable pass
	in provider.upsert_catalog so a removed size/image stops being offered
	without deleting its history."""
	disabled = 0
	for name in frappe.get_all(doctype, filters={"enabled": 1}, pluck="name"):
		if name not in seen:
			frappe.db.set_value(doctype, name, "enabled", 0)
			disabled += 1
	return disabled
