import re

import frappe
from frappe.model.document import Document

from atlas.atlas.secrets import get_secret
from atlas.atlas.ssh import run_task, upload_files

BOOTSTRAP_UPLOADS = [
	("scripts/vm-network-up.sh", "/var/lib/atlas/bin/vm-network-up.sh"),
	("scripts/vm-network-down.sh", "/var/lib/atlas/bin/vm-network-down.sh"),
	(
		"scripts/systemd/firecracker-vm@.service",
		"/etc/systemd/system/firecracker-vm@.service",
	),
]

BOOTSTRAP_ALLOWED_STATUS = {"Pending", "Bootstrapping", "Active", "Broken"}

KEY_VALUE_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.+)$")


class Server(Document):
	@frappe.whitelist()
	def bootstrap(self) -> str:
		"""Upload helpers + unit, run bootstrap-server.sh. Returns Task name."""
		if self.status not in BOOTSTRAP_ALLOWED_STATUS:
			frappe.throw(f"Cannot bootstrap from status {self.status}")

		from atlas.atlas.ssh import connection_for_server  # noqa: PLC0415

		connection = connection_for_server(self)
		upload_files(connection, _resolved_uploads())

		task = run_task(
			connection=connection,
			script="bootstrap-server.sh",
			variables={
				"FIRECRACKER_VERSION": "v1.15.1",
				"ARCHITECTURE": "x86_64",
			},
			server=self.name,
		)
		self._absorb_bootstrap_output(task.stdout)
		self.save(ignore_permissions=True)
		return task.name

	@frappe.whitelist()
	def reboot(self) -> str:
		"""Stub in phase 3. Real implementation in phase 7."""
		frappe.throw("Reboot is wired in phase 7")

	@frappe.whitelist()
	def run_task_dialog(self, script: str, variables: dict | str | None = None) -> str:
		"""Stub in phase 3. Real implementation in phase 7."""
		frappe.throw("Run Task is wired in phase 7")

	def _absorb_bootstrap_output(self, stdout: str) -> None:
		fields = {"FIRECRACKER_VERSION": "firecracker_version",
		          "KERNEL_VERSION": "kernel_version",
		          "ARCHITECTURE": "architecture"}
		for line in stdout.splitlines():
			match = KEY_VALUE_LINE.match(line.strip())
			if not match:
				continue
			key, value = match.group(1), match.group(2).strip()
			fieldname = fields.get(key)
			if fieldname:
				setattr(self, fieldname, value)


def _resolved_uploads() -> list[tuple[str, str]]:
	from atlas.atlas.ssh import SCRIPTS_DIRECTORY  # noqa: PLC0415
	resolved = []
	for local, remote in BOOTSTRAP_UPLOADS:
		# `local` is relative to the repo root; SCRIPTS_DIRECTORY ends in /scripts,
		# so strip the leading "scripts/" and re-join.
		assert local.startswith("scripts/"), local
		local_path = SCRIPTS_DIRECTORY / local[len("scripts/"):]
		resolved.append((str(local_path), remote))
	return resolved
