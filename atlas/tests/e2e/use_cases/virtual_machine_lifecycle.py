"""Use case: operate a running Firecracker VM.

Operator clicks Start / Stop / Restart / Terminate. Each is one Task running
a one-line shell script (`start-vm.sh`, `stop-vm.sh`, `terminate-vm.sh`);
Restart is `stop` + `start` orchestrated in Python, not a separate script.

This module exercises:

- Provision -> Stop -> Start -> Restart -> Terminate, with a probe on every
  state.
- Terminate again from Terminated throws.
- start while Pending throws; stop while Pending throws; restart while
  Pending throws. (These guard the operator against double-clicking the
  wrong button on a freshly inserted row.)
"""

import time

import frappe

from atlas.tests.e2e._shared import (
	assert_probe,
	ensure_image_on_server,
	ephemeral_public_key,
	expect_validation_error,
	phase,
)


def run(reuse: bool = True, keep: bool = True) -> None:
	with phase("vm-lifecycle", reuse=reuse, keep=keep) as server:
		image_doc = ensure_image_on_server(server.name)
		public_key = ephemeral_public_key()

		vm = frappe.get_doc({
			"doctype": "Virtual Machine",
			"description": "vm-lifecycle",
			"server": server.name,
			"image": image_doc.name,
			"vcpus": 1,
			"memory_megabytes": 512,
			"disk_gigabytes": 4,
			"ssh_public_key": public_key,
		}).insert(ignore_permissions=True)

		_check_pending_state_guards(vm)
		_check_full_lifecycle(server.name, vm)


def _check_pending_state_guards(vm) -> None:
	"""Before Provision, start/stop/restart all throw with a clear message."""
	with expect_validation_error("cannot start"):
		vm.start()
	with expect_validation_error("cannot stop"):
		vm.stop()
	with expect_validation_error("cannot restart"):
		vm.restart()


def _check_full_lifecycle(server_name: str, vm) -> None:
	"""Provision -> Stop -> Start -> Restart -> Terminate, with probes."""
	vm.provision()
	vm.reload()
	assert vm.status == "Running", vm.status
	first_started = vm.last_started
	assert_probe(server_name, "phase5-is-active.sh", VIRTUAL_MACHINE_NAME=vm.name)

	# Stop.
	vm.stop()
	vm.reload()
	assert vm.status == "Stopped", vm.status
	assert vm.last_stopped, "last_stopped should be set"
	assert_probe(server_name, "phase6-is-inactive.sh", VIRTUAL_MACHINE_NAME=vm.name)

	# Start.
	time.sleep(1)  # advance clock for last_started comparison
	vm.start()
	vm.reload()
	assert vm.status == "Running", vm.status
	assert vm.last_started > first_started, (
		f"last_started did not advance: {first_started} -> {vm.last_started}"
	)
	assert_probe(server_name, "phase5-is-active.sh", VIRTUAL_MACHINE_NAME=vm.name)

	# Restart (Running -> Running, two tasks).
	before_stop = vm.last_stopped
	before_start = vm.last_started
	time.sleep(1)
	result = vm.restart()
	assert result["stop_task"] and result["start_task"], result
	vm.reload()
	assert vm.status == "Running", vm.status
	assert vm.last_stopped > before_stop, "last_stopped did not advance on restart"
	assert vm.last_started > before_start, "last_started did not advance on restart"
	assert_probe(server_name, "phase5-is-active.sh", VIRTUAL_MACHINE_NAME=vm.name)

	# Terminate.
	tap_device = vm.tap_device
	vm.terminate()
	vm.reload()
	assert vm.status == "Terminated", vm.status
	assert_probe(
		server_name,
		"phase6-assert-gone.sh",
		VIRTUAL_MACHINE_NAME=vm.name,
		TAP_DEVICE=tap_device,
	)

	# Terminate from Terminated -> throw.
	with expect_validation_error("already terminated"):
		vm.terminate()
