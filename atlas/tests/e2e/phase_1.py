"""Phase 1 e2e: run_task against an operator-provided droplet."""

import time
import traceback

import frappe

from atlas.atlas.ssh import run_task
from atlas.tests.e2e._shared import MissingConfig, get_phase1_connection


def run() -> None:
	start_clock = time.monotonic()
	try:
		connection = get_phase1_connection()
	except MissingConfig as exception:
		print(str(exception))
		raise

	try:
		_check_happy_path(connection)
		_check_failure(connection)
		_check_missing_script(connection)
	except Exception:
		elapsed = time.monotonic() - start_clock
		print(f"phase-1: FAIL in {elapsed:.0f}s")
		traceback.print_exc()
		raise

	elapsed = time.monotonic() - start_clock
	print(f"phase-1: OK in {elapsed:.0f}s")


def _check_happy_path(connection: dict) -> None:
	task = run_task(
		connection=connection,
		script="phase1-probe.sh",
		variables={"NAME": "hi"},
	)
	assert task.status == "Success", f"expected Success, got {task.status}"
	assert task.exit_code == 0
	assert "hello hi" in task.stdout, f"stdout missing 'hello hi': {task.stdout!r}"


def _check_failure(connection: dict) -> None:
	caught = False
	try:
		run_task(
			connection=connection,
			script="phase1-fail.sh",
			variables={},
		)
	except frappe.ValidationError:
		caught = True
	assert caught, "phase1-fail.sh should have raised"

	task = frappe.get_last_doc("Task", filters={"script": "phase1-fail.sh"})
	assert task.status == "Failure"
	assert task.exit_code == 7
	assert "boom" in task.stderr


def _check_missing_script(connection: dict) -> None:
	caught = False
	try:
		run_task(
			connection=connection,
			script="does-not-exist.sh",
			variables={},
		)
	except frappe.ValidationError:
		caught = True
	assert caught, "missing script should have raised"

	task = frappe.get_last_doc("Task", filters={"script": "does-not-exist.sh"})
	# Clean Failure (not stuck in Running/Pending) is what "no half-written row" means
	# in practice: every code path that creates a Task row also finalizes it.
	assert task.status == "Failure", f"expected Failure, got {task.status}"
