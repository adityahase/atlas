import frappe
from frappe.model.document import Document

from atlas.atlas.digitalocean import DigitalOceanClient
from atlas.atlas.secrets import get_secret


class ServerProvider(Document):
	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Ping the DigitalOcean account endpoint."""
		token = get_secret("Server Provider", self.name, "api_token")
		client = DigitalOceanClient(token=token)
		account = client.account()
		return {"ok": True, "email": account.get("email")}

	@frappe.whitelist()
	def provision_server(self, server_name: str) -> str:
		"""Create a droplet, insert a Server row, enqueue bootstrap."""
		from atlas.atlas.server_provider import provision_server  # noqa: PLC0415

		return provision_server(self, server_name)
