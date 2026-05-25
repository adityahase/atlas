"""Phase 3 e2e: provision a real server, bootstrap, verify."""

import time
import traceback

import frappe

from atlas.atlas.server_provider import provision_server
from atlas.atlas.ssh import run_task_on_server
from atlas.tests.e2e._shared import (
	cleanup_droplet,
	get_client,
	get_image,
	get_region,
	get_size,
	get_ssh_key_id,
	get_ssh_private_key,
	sweep_old_droplets,
)


def run() -> None:
	start_clock = time.monotonic()
	client = get_client()
	sweep_old_droplets(client)

	provider = _ensure_provider()
	server_name = f"atlas-e2e-phase3-{int(time.time())}"
	server_doc = None

	try:
		provision_server(provider, server_name)
		server_doc = _wait_for_status(server_name, target={"Active", "Broken"}, timeout=600)
		assert server_doc.status == "Active", f"expected Active, got {server_doc.status}"
		assert server_doc.firecracker_version, "firecracker_version not recorded"

		bootstrap_tasks = frappe.get_all(
			"Task",
			filters={"server": server_name, "script": "bootstrap-server.sh", "status": "Success"},
		)
		assert bootstrap_tasks, "no successful bootstrap Task found"

		_assert_remote_layout(server_name)

		# Idempotency: re-bootstrap.
		server_doc.bootstrap()
	except Exception:
		elapsed = time.monotonic() - start_clock
		print(f"phase-3: FAIL in {elapsed:.0f}s")
		traceback.print_exc()
		raise
	finally:
		if server_doc and server_doc.provider_resource_id:
			cleanup_droplet(client, int(server_doc.provider_resource_id))

	elapsed = time.monotonic() - start_clock
	print(f"phase-3: OK in {elapsed:.0f}s")


def _ensure_provider() -> "frappe.model.document.Document":
	name = "atlas-e2e-provider"
	if frappe.db.exists("Server Provider", name):
		return frappe.get_doc("Server Provider", name)
	return frappe.get_doc({
		"doctype": "Server Provider",
		"provider_name": name,
		"provider_type": "DigitalOcean",
		"api_token": frappe.conf.get("atlas_do_token"),
		"ssh_key_id": get_ssh_key_id(),
		"ssh_private_key": get_ssh_private_key(),
		"default_region": get_region(),
		"default_size": get_size(),
		"default_image": get_image(),
		"is_active": 1,
	}).insert(ignore_permissions=True)


def _wait_for_status(server_name: str, target: set[str], timeout: int) -> "frappe.model.document.Document":
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		frappe.db.rollback()  # re-read
		server = frappe.get_doc("Server", server_name)
		if server.status in target:
			return server
		time.sleep(5)
	raise AssertionError(f"server {server_name} did not reach {target} within {timeout}s")


def _assert_remote_layout(server_name: str) -> None:
	task = run_task_on_server(
		server=server_name,
		script="phase3-probe.sh",
		variables={},
		timeout_seconds=30,
	)
	assert task.status == "Success"
	assert "vm-network-up.sh OK" in task.stdout
	assert "vm-network-down.sh OK" in task.stdout
	assert "firecracker-vm@.service OK" in task.stdout
