"""Phase 5 e2e: provision a Firecracker VM and verify it boots."""

import os
import subprocess
import time
import traceback

import frappe

from atlas.atlas.ssh import run_task_on_server
from atlas.tests.e2e._shared import get_client, sweep_old_droplets


def run() -> None:
	start_clock = time.monotonic()
	client = get_client()
	sweep_old_droplets(client)

	server = _pick_active_server()
	image = _pick_synced_image(server.name)

	keypair_dir = _make_ephemeral_keypair()
	public_key = (open(f"{keypair_dir}/id.pub").read()).strip()

	vm = frappe.get_doc({
		"doctype": "Virtual Machine",
		"description": "phase 5 e2e",
		"server": server.name,
		"image": image,
		"vcpus": 1,
		"memory_megabytes": 512,
		"disk_gigabytes": 4,
		"ssh_public_key": public_key,
	}).insert(ignore_permissions=True)

	try:
		# Negative: temporarily move the image aside.
		_move_image_aside(server.name, image)
		raised = False
		try:
			vm.provision()
		except frappe.ValidationError as exception:
			raised = True
			assert "not present" in str(exception).lower() or "missing" in str(exception).lower()
		assert raised, "provision should have raised when image absent"
		vm.reload()
		# Probe failure already marked Failed; ok.
		_move_image_back(server.name, image)

		# Recover state for the positive path.
		vm.status = "Pending"
		vm.save(ignore_permissions=True)

		vm.provision()
		vm.reload()
		assert vm.status == "Running", vm.status
		assert vm.last_started

		_assert_is_active_on_server(server.name, vm.name)
	except Exception:
		elapsed = time.monotonic() - start_clock
		print(f"phase-5: FAIL in {elapsed:.0f}s")
		traceback.print_exc()
		raise

	elapsed = time.monotonic() - start_clock
	print(f"phase-5: OK in {elapsed:.0f}s")


def _pick_active_server() -> "frappe.model.document.Document":
	names = frappe.get_all("Server", filters={"status": "Active"}, pluck="name")
	if not names:
		raise AssertionError("no Active Server available; run phase 3 first")
	return frappe.get_doc("Server", names[0])


def _pick_synced_image(server_name: str) -> str:
	names = frappe.get_all("Virtual Machine Image", filters={"is_active": 1}, pluck="name")
	if not names:
		raise AssertionError("no Virtual Machine Image; run phase 4 first")
	return names[0]


def _make_ephemeral_keypair() -> str:
	directory = "/tmp/atlas-e2e-keys"
	os.makedirs(directory, exist_ok=True)
	key_path = f"{directory}/id"
	if not os.path.exists(key_path):
		subprocess.run(
			["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path],
			check=True,
		)
	os.chmod(key_path, 0o600)
	return directory


def _move_image_aside(server_name: str, image: str) -> None:
	image_doc = frappe.get_doc("Virtual Machine Image", image)
	task = run_task_on_server(
		server=server_name,
		script="phase5-move-image.sh",
		variables={
			"IMAGE_NAME": image_doc.image_name,
			"ROOTFS_FILENAME": image_doc.rootfs_filename,
			"DIRECTION": "aside",
		},
		timeout_seconds=15,
	)
	assert task.status == "Success"


def _move_image_back(server_name: str, image: str) -> None:
	image_doc = frappe.get_doc("Virtual Machine Image", image)
	task = run_task_on_server(
		server=server_name,
		script="phase5-move-image.sh",
		variables={
			"IMAGE_NAME": image_doc.image_name,
			"ROOTFS_FILENAME": image_doc.rootfs_filename,
			"DIRECTION": "back",
		},
		timeout_seconds=15,
	)
	assert task.status == "Success"


def _assert_is_active_on_server(server_name: str, vm_name: str) -> None:
	task = run_task_on_server(
		server=server_name,
		script="phase5-is-active.sh",
		variables={"VIRTUAL_MACHINE_NAME": vm_name},
		timeout_seconds=15,
	)
	assert task.status == "Success", task.stderr
