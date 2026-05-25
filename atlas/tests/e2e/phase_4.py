"""Phase 4 e2e: sync an image to a real server."""

import time
import traceback

import frappe

from atlas.atlas.ssh import run_task_on_server
from atlas.tests.e2e._shared import (
	cleanup_droplet,
	get_client,
	sweep_old_droplets,
)

# Public Firecracker CI Ubuntu 24.04 artifacts (pinned for stability).
DEFAULT_IMAGE = {
	"image_name": "ubuntu-24.04",
	"description": "Firecracker CI Ubuntu 24.04 rootfs",
	"kernel_url": "https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.10/x86_64/vmlinux-6.1.102",
	"kernel_filename": "vmlinux-6.1.102",
	"kernel_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
	"rootfs_url": "https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.10/x86_64/ubuntu-24.04.squashfs",
	"rootfs_filename": "ubuntu-24.04.ext4",
	"rootfs_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
	"default_disk_gigabytes": 4,
}


def run() -> None:
	"""Requires an Active Server already provisioned (from phase 3)."""
	start_clock = time.monotonic()
	client = get_client()
	sweep_old_droplets(client)

	server = _pick_active_server()
	image = _ensure_image()

	try:
		task_name = image.sync_to_server(server.name)
		task = _wait_for_task(task_name, timeout=900)
		assert task.status == "Success", f"sync-image failed: {task.stderr[:500]}"

		_assert_image_on_server(server.name, image)

		# Idempotency: re-sync should short-circuit.
		task_name = image.sync_to_server(server.name)
		task = _wait_for_task(task_name, timeout=120)
		assert task.status == "Success"
		assert "already" in task.stdout.lower()
	except Exception:
		elapsed = time.monotonic() - start_clock
		print(f"phase-4: FAIL in {elapsed:.0f}s")
		traceback.print_exc()
		raise

	elapsed = time.monotonic() - start_clock
	print(f"phase-4: OK in {elapsed:.0f}s")


def _pick_active_server() -> "frappe.model.document.Document":
	names = frappe.get_all("Server", filters={"status": "Active"}, pluck="name")
	if not names:
		raise AssertionError("no Active Server available; run phase 3 first")
	return frappe.get_doc("Server", names[0])


def _ensure_image() -> "frappe.model.document.Document":
	name = DEFAULT_IMAGE["image_name"]
	if frappe.db.exists("Virtual Machine Image", name):
		return frappe.get_doc("Virtual Machine Image", name)
	doc = {"doctype": "Virtual Machine Image", **DEFAULT_IMAGE, "is_active": 1}
	return frappe.get_doc(doc).insert(ignore_permissions=True)


def _wait_for_task(task_name: str, timeout: int) -> "frappe.model.document.Document":
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		frappe.db.rollback()
		task = frappe.get_doc("Task", task_name)
		if task.status in ("Success", "Failure"):
			return task
		time.sleep(5)
	raise AssertionError(f"task {task_name} did not finish within {timeout}s")


def _assert_image_on_server(server_name: str, image) -> None:
	task = run_task_on_server(
		server=server_name,
		script="phase4-probe.sh",
		variables={
			"IMAGE_NAME": image.image_name,
			"KERNEL_FILENAME": image.kernel_filename,
			"ROOTFS_FILENAME": image.rootfs_filename,
			"DEFAULT_DISK_GB": str(image.default_disk_gigabytes),
		},
		timeout_seconds=60,
	)
	assert task.status == "Success", f"probe failed: {task.stderr[:500]}"
