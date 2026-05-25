from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.networking import carve_virtual_machine_range


def _make_provider() -> "frappe.model.document.Document":
	name = "test-provider-server"
	if frappe.db.exists("Server Provider", name):
		return frappe.get_doc("Server Provider", name)
	return frappe.get_doc({
		"doctype": "Server Provider",
		"provider_name": name,
		"provider_type": "DigitalOcean",
		"api_token": "dop_v1_fake",
		"ssh_key_id": "fp:fingerprint",
		"ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n",
		"default_region": "blr1",
		"default_size": "s-2vcpu-4gb-intel",
		"default_image": "ubuntu-24-04-x64",
		"is_active": 1,
	}).insert(ignore_permissions=True)


def _make_server(suffix: str = "1") -> "frappe.model.document.Document":
	provider = _make_provider()
	name = f"test-server-{suffix}"
	if frappe.db.exists("Server", name):
		return frappe.get_doc("Server", name)
	return frappe.get_doc({
		"doctype": "Server",
		"server_name": name,
		"provider": provider.name,
		"provider_resource_id": "1",
		"region": provider.default_region,
		"size": provider.default_size,
		"ipv4_address": "10.0.0.5",
		"ipv6_address": "2a03:b0c0:abcd:1234::1",
		"ipv6_prefix": "2a03:b0c0:abcd:1234::/64",
		"ipv6_virtual_machine_range": "2a03:b0c0:abcd:1234::/124",
		"status": "Bootstrapping",
	}).insert(ignore_permissions=True)


class TestNetworking(IntegrationTestCase):
	def test_carve_virtual_machine_range(self) -> None:
		self.assertEqual(
			carve_virtual_machine_range("2a03:b0c0:abcd:1234::/64"),
			"2a03:b0c0:abcd:1234::/124",
		)
		self.assertEqual(
			carve_virtual_machine_range("2001:db8::/64"),
			"2001:db8::/124",
		)


class TestServerBootstrap(IntegrationTestCase):
	def setUp(self) -> None:
		self.server = _make_server("bootstrap")

	def test_bootstrap_uploads_helpers_then_runs_script(self) -> None:
		from atlas.atlas.doctype.server import server as server_module

		fake_task = MagicMock()
		fake_task.name = "task-x"
		fake_task.stdout = ""

		with patch.object(server_module, "upload_files") as upload:
			with patch.object(server_module, "run_task", return_value=fake_task) as run:
				with patch(
					"atlas.atlas.ssh.connection_for_server",
					return_value={"host": "x", "ssh_private_key": "k", "user": "root"},
				):
					self.server.bootstrap()

		upload.assert_called_once()
		# The first call must be the upload, the second the script run.
		args, _ = run.call_args
		_ = args
		run.assert_called_once()

	def test_bootstrap_parses_trailing_key_values(self) -> None:
		from atlas.atlas.doctype.server import server as server_module

		stdout = (
			"+ some bash trace\n"
			"FIRECRACKER_VERSION=1.15.1\n"
			"KERNEL_VERSION=6.8.0-31-generic\n"
			"ARCHITECTURE=x86_64\n"
		)
		fake_task = MagicMock()
		fake_task.name = "task-y"
		fake_task.stdout = stdout

		with patch.object(server_module, "upload_files"):
			with patch.object(server_module, "run_task", return_value=fake_task):
				with patch(
					"atlas.atlas.ssh.connection_for_server",
					return_value={"host": "x", "ssh_private_key": "k", "user": "root"},
				):
					self.server.bootstrap()
		self.server.reload()
		self.assertEqual(self.server.firecracker_version, "1.15.1")
		self.assertEqual(self.server.kernel_version, "6.8.0-31-generic")
		self.assertEqual(self.server.architecture, "x86_64")
