from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.script_uploads import files_to_upload


def _make_image() -> "frappe.model.document.Document":
	name = "test-image"
	if frappe.db.exists("Virtual Machine Image", name):
		return frappe.get_doc("Virtual Machine Image", name)
	return frappe.get_doc({
		"doctype": "Virtual Machine Image",
		"image_name": name,
		"kernel_url": "https://example.com/vmlinux",
		"kernel_filename": "vmlinux-1.0",
		"kernel_sha256": "a" * 64,
		"rootfs_url": "https://example.com/rootfs.squashfs",
		"rootfs_filename": "rootfs.ext4",
		"rootfs_sha256": "b" * 64,
		"default_disk_gigabytes": 4,
		"is_active": 1,
	}).insert(ignore_permissions=True)


def _make_provider_and_server(server_name: str, status: str) -> None:
	provider_name = "test-provider-image"
	if not frappe.db.exists("Server Provider", provider_name):
		frappe.get_doc({
			"doctype": "Server Provider",
			"provider_name": provider_name,
			"provider_type": "DigitalOcean",
			"api_token": "fake",
			"ssh_key_id": "fp",
			"ssh_private_key": "k",
			"default_region": "blr1",
			"default_size": "s",
			"default_image": "i",
			"is_active": 1,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Server", server_name):
		frappe.get_doc({
			"doctype": "Server",
			"server_name": server_name,
			"provider": provider_name,
			"status": status,
		}).insert(ignore_permissions=True)


class TestVirtualMachineImage(IntegrationTestCase):
	def setUp(self) -> None:
		self.image = _make_image()

	def test_validate_urls_https(self) -> None:
		bad = frappe.get_doc({
			"doctype": "Virtual Machine Image",
			"image_name": "bad-image",
			"kernel_url": "http://example.com/vmlinux",
			"kernel_filename": "vmlinux-1.0",
			"kernel_sha256": "a" * 64,
			"rootfs_url": "https://example.com/rootfs.squashfs",
			"rootfs_filename": "rootfs.ext4",
			"rootfs_sha256": "b" * 64,
			"default_disk_gigabytes": 4,
			"is_active": 1,
		})
		with self.assertRaises(frappe.ValidationError):
			bad.insert(ignore_permissions=True)

	def test_sync_to_server_enqueues_task(self) -> None:
		_make_provider_and_server("test-srv-sync", "Active")
		with patch("frappe.enqueue") as enqueue:
			task_name = self.image.sync_to_server("test-srv-sync")
		enqueue.assert_called_once()
		task = frappe.get_doc("Task", task_name)
		self.assertEqual(task.status, "Pending")
		self.assertEqual(task.script, "sync-image.sh")
		self.assertEqual(task.server, "test-srv-sync")

	def test_sync_to_all_servers_enqueues_one_per_active(self) -> None:
		_make_provider_and_server("srv-active-1", "Active")
		_make_provider_and_server("srv-broken-1", "Broken")
		_make_provider_and_server("srv-archived-1", "Archived")
		with patch("frappe.enqueue") as enqueue:
			tasks = self.image.sync_to_all_servers()
		# Active servers are: srv-active-1 plus any previous Active servers
		# from other tests; we filter to the ones we just created.
		our_tasks = [
			t for t in tasks
			if frappe.db.get_value("Task", t, "server") == "srv-active-1"
		]
		self.assertEqual(len(our_tasks), 1)
		# enqueue called once per Active server in the system (>=1 from ours).
		self.assertGreaterEqual(enqueue.call_count, 1)

	def test_files_to_upload_for_sync_image(self) -> None:
		uploads = files_to_upload("sync-image.sh")
		self.assertTrue(any("atlas-network.service" in remote for _, remote in uploads))
