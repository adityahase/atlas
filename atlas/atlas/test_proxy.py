import contextlib
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas import proxy
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
	_new_vm,
)


def _purge() -> None:
	# Tasks are deliberately NOT purged: they're append-only audit rows, every
	# assertion filters by the per-test VM name (a fresh UUID), so stale Tasks
	# can never match — and deleting them takes a FOR UPDATE NOWAIT lock that
	# flakes under the full-suite's transaction contention.
	for name in frappe.get_all("Subdomain", pluck="name"):
		frappe.delete_doc("Subdomain", name, force=1, ignore_permissions=True)
	for name in frappe.get_all("Virtual Machine", pluck="name"):
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


def _make_subdomain(subdomain: str, vm: str, region: str, **overrides):
	doc = {"doctype": "Subdomain", "subdomain": subdomain, "virtual_machine": vm, "region": region}
	doc.update(overrides)
	return frappe.get_doc(doc).insert(ignore_permissions=True)


def _proxy_vm(region: str = "blr1"):
	"""A VM marked as a proxy in `region` — the reconcile target."""
	return _new_vm(is_proxy=1, region=region)


@contextlib.contextmanager
def _mock_ssh(responses):
	"""Patch the guest-SSH plumbing proxy.py uses. `responses` is a list of
	(stdout, stderr, exit_code) tuples returned by successive run_ssh calls.
	Yields the run_ssh MagicMock so a test can assert the commands/stdin sent."""
	run_ssh = MagicMock(side_effect=list(responses))
	key_cm = MagicMock()
	key_cm.__enter__ = MagicMock(return_value="/tmp/fake.key")
	key_cm.__exit__ = MagicMock(return_value=False)
	with (
		patch.object(proxy, "run_ssh", run_ssh),
		patch.object(proxy, "ssh_key_file", return_value=key_cm),
		patch.object(proxy, "connection_for_guest", return_value=MagicMock(ssh_private_key="KEY")),
	):
		yield run_ssh


class TestCanonicalJson(IntegrationTestCase):
	def test_matches_lua_persist_format(self) -> None:
		# Byte-identical to persist.lua / the compose harness expectation: sorted
		# keys, 2-space indent, one per line, trailing newline.
		out = proxy.canonical_json({"b": "2400::b", "a": "2400::a"})
		self.assertEqual(out, '{\n  "a": "2400::a",\n  "b": "2400::b"\n}\n')

	def test_empty_map_is_brace_brace_newline(self) -> None:
		self.assertEqual(proxy.canonical_json({}), "{}\n")


class TestReconcile(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_no_drift_skips_sync(self) -> None:
		proxy_vm = _proxy_vm()
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		desired = proxy.canonical_json({"acme": site_vm.ipv6_address})
		# The guest's live /map already equals desired → no POST.
		with _mock_ssh([(desired, "", 0)]) as run_ssh:
			synced = proxy.reconcile_proxy(proxy_vm.name)
		self.assertFalse(synced)
		self.assertEqual(run_ssh.call_count, 1)  # GET /map only

	def test_drift_triggers_bulk_sync_with_canonical_body(self) -> None:
		proxy_vm = _proxy_vm()
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		desired = proxy.canonical_json({"acme": site_vm.ipv6_address})
		# Live map is empty (fresh proxy) → drifted → POST /sync.
		with _mock_ssh([("{}\n", "", 0), ('{"synced":true}', "", 0)]) as run_ssh:
			synced = proxy.reconcile_proxy(proxy_vm.name)
		self.assertTrue(synced)
		self.assertEqual(run_ssh.call_count, 2)
		# The second call is the /sync; its stdin body is the canonical desired map.
		_, sync_kwargs = run_ssh.call_args_list[1]
		self.assertEqual(sync_kwargs["stdin"], desired)
		sync_command = run_ssh.call_args_list[1].args[2]
		self.assertIn("/sync", sync_command)
		self.assertIn("--data-binary", sync_command)

	def test_drift_records_a_task_row(self) -> None:
		proxy_vm = _proxy_vm()
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		with _mock_ssh([("{}\n", "", 0), ("ok", "", 0)]):
			proxy.reconcile_proxy(proxy_vm.name)
		tasks = frappe.get_all(
			"Task", filters={"virtual_machine": proxy_vm.name, "script": "proxy-sync"}, pluck="status"
		)
		self.assertEqual(tasks, ["Success"])

	def test_sync_failure_raises_and_records_failure(self) -> None:
		proxy_vm = _proxy_vm()
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		with _mock_ssh([("{}\n", "", 0), ("", "boom", 1)]):
			with self.assertRaises(frappe.ValidationError):
				proxy.reconcile_proxy(proxy_vm.name)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": proxy_vm.name, "script": "proxy-sync"}, pluck="status"
		)
		self.assertEqual(status, ["Failure"])

	def test_reconcile_region_targets_every_proxy_vm(self) -> None:
		proxy_a = _proxy_vm("blr1")
		proxy_b = _proxy_vm("blr1")
		_proxy_vm("sgp1")  # other region — must be untouched
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		# Both blr1 proxies are empty → both drift → both synced.
		with _mock_ssh([("{}\n", "", 0), ("ok", "", 0)] * 2):
			synced = proxy.reconcile_region("blr1")
		self.assertEqual(set(synced), {proxy_a.name, proxy_b.name})

	def test_reconcile_region_isolates_one_unreachable_proxy(self) -> None:
		proxy_a = _proxy_vm("blr1")
		proxy_b = _proxy_vm("blr1")
		site_vm = _new_vm()
		_make_subdomain("acme", site_vm.name, "blr1")
		# First proxy's GET raises (guest wedged); the loop must still reach the
		# second and sync it. Order isn't guaranteed, so make BOTH paths viable:
		# one raises on GET, one syncs cleanly.
		desired = proxy.canonical_json({"acme": site_vm.ipv6_address})

		def ssh_side_effect(connection, key_path, command, timeout_seconds, stdin=None):
			# Identify the proxy by the mocked connection's host marker.
			if connection.host == "DEAD":
				raise RuntimeError("guest unreachable")
			if "GET" in command or "/map" in command:
				return ("{}\n", "", 0)
			return ("ok", "", 0)

		def conn_for(vm):
			host = "DEAD" if vm.name == proxy_a.name else "OK"
			return MagicMock(host=host, ssh_private_key="KEY")

		key_cm = MagicMock()
		key_cm.__enter__ = MagicMock(return_value="/tmp/fake.key")
		key_cm.__exit__ = MagicMock(return_value=False)
		with (
			patch.object(proxy, "run_ssh", MagicMock(side_effect=ssh_side_effect)),
			patch.object(proxy, "ssh_key_file", return_value=key_cm),
			patch.object(proxy, "connection_for_guest", side_effect=conn_for),
		):
			synced = proxy.reconcile_region("blr1")
		# The dead one is skipped; the healthy one still synced.
		self.assertEqual(synced, [proxy_b.name])
		_ = desired


class TestPushCert(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_push_cert_writes_both_pems_and_reloads(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		# 3 SSH calls: write fullchain, write privkey, reload.
		with _mock_ssh([("", "", 0), ("", "", 0), ("", "", 0)]) as run_ssh:
			proxy.push_cert(proxy_vm.name, fullchain="FULL", privkey="PRIV")
		self.assertEqual(run_ssh.call_count, 3)
		commands = [c.args[2] for c in run_ssh.call_args_list]
		stdins = [c.kwargs.get("stdin") for c in run_ssh.call_args_list]
		# Region-scoped cert dir, private key via stdin (never in argv), reload last.
		self.assertIn("fullchain.pem", commands[0])
		self.assertIn("blr1", commands[0])
		self.assertEqual(stdins[0], "FULL")
		self.assertIn("privkey.pem", commands[1])
		self.assertIn("0600", commands[1])
		self.assertEqual(stdins[1], "PRIV")
		# Last command repoints the flat cert symlink at this region's dir, then
		# reloads — so the pushed cert (region-scoped) is what nginx serves.
		self.assertIn("ln -sfn", commands[2])
		self.assertIn("blr1/fullchain.pem", commands[2])
		self.assertIn(f"{proxy.CERT_DIRECTORY}/fullchain.pem", commands[2])
		self.assertIn("nginx", commands[2])
		self.assertIn("reload", commands[2])

	def test_push_cert_records_task(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		with _mock_ssh([("", "", 0), ("", "", 0), ("", "", 0)]):
			proxy.push_cert(proxy_vm.name, fullchain="FULL", privkey="PRIV")
		status = frappe.get_all(
			"Task", filters={"virtual_machine": proxy_vm.name, "script": "proxy-push-cert"}, pluck="status"
		)
		self.assertEqual(status, ["Success"])

	def test_push_cert_raises_on_reload_failure(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		with _mock_ssh([("", "", 0), ("", "", 0), ("", "nginx: bad config", 1)]):
			with self.assertRaises(frappe.ValidationError):
				proxy.push_cert(proxy_vm.name, fullchain="FULL", privkey="PRIV")


@contextlib.contextmanager
def _mock_build_ssh(run_ssh_responses):
	"""Like _mock_ssh, but also patches run_scp (build_proxy uploads the tree).
	Yields (run_ssh, run_scp) so a test can assert what was uploaded and run."""
	run_ssh = MagicMock(side_effect=list(run_ssh_responses))
	run_scp = MagicMock(return_value=None)
	key_cm = MagicMock()
	key_cm.__enter__ = MagicMock(return_value="/tmp/fake.key")
	key_cm.__exit__ = MagicMock(return_value=False)
	with (
		patch.object(proxy, "run_ssh", run_ssh),
		patch.object(proxy, "run_scp", run_scp),
		patch.object(proxy, "ssh_key_file", return_value=key_cm),
		patch.object(proxy, "connection_for_guest", return_value=MagicMock(ssh_private_key="KEY")),
	):
		yield run_ssh, run_scp


class TestProxyTreeUploads(IntegrationTestCase):
	"""The file enumeration is pure (reads the repo's committed proxy/ tree), so
	it's unit-coverable in milliseconds with no host."""

	def test_includes_build_script_and_stack_excludes_test_harness(self) -> None:
		uploads = proxy._proxy_tree_uploads()
		remotes = [remote for _, remote in uploads]
		# The build script and every stack file the guest needs are present...
		self.assertTrue(any(r.endswith("/build.sh") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/conf/nginx.conf") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/lua/router.lua") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/lua/admin.lua") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/lua/persist.lua") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/html/not_found.html") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/guest/atlas-proxy.service") for r in remotes), remotes)
		# ...and the dev-only compose harness + caches are excluded.
		self.assertFalse(any("/test/" in r for r in remotes), remotes)
		self.assertFalse(any("__pycache__" in r for r in remotes), remotes)

	def test_remotes_are_under_one_staging_dir_preserving_layout(self) -> None:
		uploads = proxy._proxy_tree_uploads()
		for _, remote in uploads:
			self.assertTrue(remote.startswith(proxy.REMOTE_PROXY_DIRECTORY + "/"), remote)
		# build.sh sits at the staging root (so it finds its sibling conf/lua/...).
		build = next(r for _, r in uploads if r.endswith("/build.sh"))
		self.assertEqual(build, f"{proxy.REMOTE_PROXY_DIRECTORY}/build.sh")


class TestBuildProxy(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_uploads_tree_then_builds_writes_region_and_starts(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		# 2 SSH calls: mkdir staging dirs, then the build+region+restart command.
		with _mock_build_ssh([("", "", 0), ("built", "", 0)]) as (run_ssh, run_scp):
			proxy.build_proxy(proxy_vm.name)
		# Every committed proxy file was scp'd up.
		self.assertEqual(run_scp.call_count, len(proxy._proxy_tree_uploads()))
		self.assertGreater(run_scp.call_count, 5)
		# First SSH is the mkdir; the build SSH writes the region and restarts.
		self.assertIn("mkdir -p", run_ssh.call_args_list[0].args[2])
		build_command = run_ssh.call_args_list[1].args[2]
		self.assertIn("build.sh", build_command)
		self.assertIn("blr1", build_command)
		self.assertIn(proxy.REGION_FILE, build_command)
		# The build must NOT repoint the cert symlink: build.sh leaves it on the
		# _placeholder cert (which exists) so nginx starts; push_cert repoints to
		# certs/<region>/ only after the real cert lands there. Repointing here
		# would dangle the symlink and nginx would fail to load its cert at start.
		self.assertNotIn("ln -sfn", build_command)
		self.assertIn("systemctl restart atlas-proxy.service", build_command)

	def test_records_a_task_row(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		with _mock_build_ssh([("", "", 0), ("built", "", 0)]):
			proxy.build_proxy(proxy_vm.name)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": proxy_vm.name, "script": "proxy-build"}, pluck="status"
		)
		self.assertEqual(status, ["Success"])

	def test_build_failure_raises_and_records_failure(self) -> None:
		proxy_vm = _proxy_vm("blr1")
		with _mock_build_ssh([("", "", 0), ("", "configure: error", 1)]):
			with self.assertRaises(frappe.ValidationError):
				proxy.build_proxy(proxy_vm.name)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": proxy_vm.name, "script": "proxy-build"}, pluck="status"
		)
		self.assertEqual(status, ["Failure"])

	def test_non_proxy_vm_is_rejected(self) -> None:
		plain_vm = _new_vm()  # is_proxy unset
		with self.assertRaises(frappe.ValidationError):
			proxy.build_proxy(plain_vm.name)
