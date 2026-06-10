import contextlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas import bench_image
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
	_new_vm,
)


def _purge() -> None:
	# Tasks are append-only audit rows (not purged); every assertion filters by
	# the per-test VM name (a fresh UUID), so stale Tasks never match. Same
	# discipline as test_proxy._purge.
	for name in frappe.get_all("Virtual Machine", pluck="name"):
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


@contextlib.contextmanager
def _mock_build_ssh(build_result):
	"""Patch the guest-SSH plumbing build_bench uses. Yields (run_ssh, run_scp,
	run_detached).

	The long bake now runs through the shared transport.run_detached helper (its
	setsid+nohup + marker-poll mechanics are unit-tested in test_ssh_transport); here
	we patch `bench_image.run_detached` to return `build_result` directly, so this
	suite covers build_bench's own logic (upload mapping, Task record, fail-loud)
	without re-simulating the poll loop. `run_ssh` still handles the short mkdir
	call; `run_scp` the uploads."""
	run_ssh = MagicMock(return_value=("", "", 0))
	run_scp = MagicMock(return_value=None)
	run_detached = MagicMock(return_value=build_result)
	key_cm = MagicMock()
	key_cm.__enter__ = MagicMock(return_value="/tmp/fake.key")
	key_cm.__exit__ = MagicMock(return_value=False)
	with (
		patch.object(bench_image, "run_ssh", run_ssh),
		patch.object(bench_image, "run_scp", run_scp),
		patch.object(bench_image, "run_detached", run_detached),
		patch.object(bench_image, "ssh_key_file", return_value=key_cm),
		patch.object(bench_image, "connection_for_guest", return_value=MagicMock(ssh_private_key="KEY")),
	):
		yield run_ssh, run_scp, run_detached


class TestBenchTreeUploads(IntegrationTestCase):
	"""The file enumeration is pure (reads the repo's committed bench/ tree), so
	it's unit-coverable in milliseconds with no host."""

	def test_includes_build_script_and_bench_toml(self) -> None:
		uploads = bench_image._bench_tree_uploads()
		remotes = [remote for _, remote in uploads]
		self.assertTrue(any(r.endswith("/build.sh") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/bench.toml") for r in remotes), remotes)
		# No caches leak into the upload set.
		self.assertFalse(any("__pycache__" in r for r in remotes), remotes)

	def test_remotes_are_under_one_staging_dir_with_build_at_root(self) -> None:
		uploads = bench_image._bench_tree_uploads()
		for _, remote in uploads:
			self.assertTrue(remote.startswith(bench_image.REMOTE_BENCH_DIRECTORY + "/"), remote)
		# build.sh sits at the staging root so it finds its sibling bench.toml.
		build = next(r for _, r in uploads if r.endswith("/build.sh"))
		self.assertEqual(build, f"{bench_image.REMOTE_BENCH_DIRECTORY}/build.sh")


class TestBuildBench(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_uploads_tree_then_runs_build(self) -> None:
		vm = _new_vm()
		with _mock_build_ssh(("baked", "", 0)) as (run_ssh, run_scp, run_detached):
			bench_image.build_bench(vm.name)
		# Every committed bench/ file was scp'd up.
		self.assertEqual(run_scp.call_count, len(bench_image._bench_tree_uploads()))
		self.assertIn("mkdir -p", run_ssh.call_args_list[0].args[2])
		# The build runs through run_detached (survives a dropped SSH) — not a plain
		# foreground build.sh whose life is tied to the connection. The command it
		# hands off runs build.sh, with its own log/done marker paths.
		run_detached.assert_called_once()
		self.assertIn("build.sh", run_detached.call_args.args[2])
		self.assertEqual(run_detached.call_args.kwargs["log_path"], bench_image._BUILD_LOG)
		self.assertEqual(run_detached.call_args.kwargs["done_path"], bench_image._BUILD_DONE)

	def test_records_a_task_row(self) -> None:
		vm = _new_vm()
		with _mock_build_ssh(("baked", "", 0)):
			bench_image.build_bench(vm.name)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": vm.name, "script": "bench-build"}, pluck="status"
		)
		self.assertEqual(status, ["Success"])

	def test_build_failure_raises_and_records_failure(self) -> None:
		vm = _new_vm()
		# run_detached reports a non-zero exit → build_bench throws.
		with _mock_build_ssh(("bench init: error", "", 1)):
			with self.assertRaises(frappe.ValidationError):
				bench_image.build_bench(vm.name)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": vm.name, "script": "bench-build"}, pluck="status"
		)
		self.assertEqual(status, ["Failure"])
