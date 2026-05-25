import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.digitalocean import (
	DigitalOceanClient,
	DigitalOceanError,
	public_ipv4,
	public_ipv6,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "digitalocean"


def _fixture(name: str) -> dict:
	return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


class _FakeResponse:
	def __init__(self, status_code: int, body: dict | None = None):
		self.status_code = status_code
		self._body = body or {}
		self.text = json.dumps(self._body) if body is not None else ""
		self.content = self.text.encode() if self.text else b""

	def json(self):
		return self._body


class TestDigitalOceanClient(IntegrationTestCase):
	def setUp(self) -> None:
		self.client = DigitalOceanClient(token="dop_v1_test")

	def test_account_ok(self) -> None:
		fake = _FakeResponse(200, _fixture("account"))
		with patch("atlas.atlas.digitalocean.requests.request", return_value=fake) as request:
			account = self.client.account()
		self.assertEqual(account["email"], "test@example.com")
		_, kwargs = request.call_args
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer dop_v1_test")

	def test_account_bad_token(self) -> None:
		fake = _FakeResponse(401, _fixture("error_unauthorized"))
		with patch("atlas.atlas.digitalocean.requests.request", return_value=fake):
			with self.assertRaises(DigitalOceanError):
				self.client.account()

	def test_create_droplet_request_shape(self) -> None:
		fake = _FakeResponse(202, _fixture("droplet_new"))
		with patch("atlas.atlas.digitalocean.requests.request", return_value=fake) as request:
			self.client.create_droplet(
				name="atlas-e2e-x",
				region="blr1",
				size="s-2vcpu-4gb-intel",
				image="ubuntu-24-04-x64",
				ssh_key_ids=["12:34:56"],
				tags=["atlas-e2e"],
				ipv6=True,
			)
		_, kwargs = request.call_args
		body = kwargs["json"]
		self.assertEqual(body["name"], "atlas-e2e-x")
		self.assertEqual(body["region"], "blr1")
		self.assertEqual(body["size"], "s-2vcpu-4gb-intel")
		self.assertEqual(body["image"], "ubuntu-24-04-x64")
		self.assertEqual(body["ssh_keys"], ["12:34:56"])
		self.assertEqual(body["tags"], ["atlas-e2e"])
		self.assertTrue(body["ipv6"])

	def test_wait_for_active_polls_until_active(self) -> None:
		responses = [
			_FakeResponse(200, _fixture("droplet_new")),
			_FakeResponse(200, _fixture("droplet_new")),
			_FakeResponse(200, _fixture("droplet_active")),
		]
		with patch("atlas.atlas.digitalocean.requests.request", side_effect=responses):
			with patch("atlas.atlas.digitalocean.time.sleep"):
				droplet = self.client.wait_for_active(412345678, timeout_seconds=60)
		self.assertEqual(droplet["status"], "active")

	def test_wait_for_active_times_out(self) -> None:
		fake = _FakeResponse(200, _fixture("droplet_new"))
		with patch("atlas.atlas.digitalocean.requests.request", return_value=fake):
			with patch("atlas.atlas.digitalocean.time.sleep"):
				with patch(
					"atlas.atlas.digitalocean.time.monotonic",
					side_effect=[0, 1, 1000],
				):
					with self.assertRaises(DigitalOceanError):
						self.client.wait_for_active(412345678, timeout_seconds=60)

	def test_delete_droplet_treats_404_as_success(self) -> None:
		fake = _FakeResponse(404)
		with patch("atlas.atlas.digitalocean.requests.request", return_value=fake):
			self.client.delete_droplet(412345678)

	def test_public_ipv6_from_droplet_fixture(self) -> None:
		droplet = _fixture("droplet_active")["droplet"]
		host, cidr = public_ipv6(droplet)
		self.assertEqual(host, "2a03:b0c0:abcd:1234::1")
		self.assertEqual(cidr, "2a03:b0c0:abcd:1234::/64")

	def test_public_ipv4_from_droplet_fixture(self) -> None:
		droplet = _fixture("droplet_active")["droplet"]
		self.assertEqual(public_ipv4(droplet), "139.59.1.2")
