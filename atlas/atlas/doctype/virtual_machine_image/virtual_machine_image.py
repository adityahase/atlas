import json

import frappe
from frappe.model.document import Document


class VirtualMachineImage(Document):
	def validate(self) -> None:
		for field in ("kernel_url", "rootfs_url"):
			value = self.get(field) or ""
			if value and not value.startswith("https://"):
				frappe.throw(f"{field} must be an https:// URL, got: {value}")

	@frappe.whitelist()
	def sync_to_all_servers(self) -> list[str]:
		"""Enqueue one sync Task per Active server. Returns Task names."""
		servers = frappe.get_all(
			"Server", filters={"status": "Active"}, pluck="name"
		)
		return [self.sync_to_server(server) for server in servers]

	@frappe.whitelist()
	def sync_to_server(self, server_name: str) -> str:
		"""Insert a Pending Task row and enqueue execute_task. Returns Task name."""
		variables = {
			"IMAGE_NAME": self.image_name,
			"KERNEL_URL": self.kernel_url,
			"KERNEL_FILENAME": self.kernel_filename,
			"KERNEL_SHA256": self.kernel_sha256,
			"ROOTFS_URL": self.rootfs_url,
			"ROOTFS_FILENAME": self.rootfs_filename,
			"ROOTFS_SHA256": self.rootfs_sha256,
			"DEFAULT_DISK_GB": str(self.default_disk_gigabytes),
			"GUEST_NETWORK_UNIT": "/tmp/atlas/atlas-network.service",
		}
		task = frappe.get_doc({
			"doctype": "Task",
			"server": server_name,
			"script": "sync-image.sh",
			"variables": json.dumps(variables, sort_keys=True),
			"status": "Pending",
			"triggered_by": frappe.session.user if frappe.session else "Administrator",
		}).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.enqueue(
			"atlas.atlas.ssh.execute_task",
			queue="long",
			timeout=1800,
			task_name=task.name,
		)
		return task.name
