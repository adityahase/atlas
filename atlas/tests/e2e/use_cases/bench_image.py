"""Operator action (and host-bound proof): bake the golden bench image.

Provisions a plain Ubuntu VM, builds bench-cli + `bench init` inside it over
guest-SSH (`atlas.atlas.bench_image.build_bench`), stops it, and snapshots it.
That snapshot is the reusable "golden bench image" self-serve site VMs clone
from (`Virtual Machine Snapshot.clone_to_new_vm`) — the build-in-guest +
snapshot pattern the proxy uses, applied to bench (spec/08-images.md).

This is the ONE host fact the golden image exists to prove:
a VM baked this way actually has a working bench — `bench --version` responds
over guest-SSH after the build. Everything else about the image (the routing
identity, the site) is per-VM and lives in deploy-site.py (spec/14-self-serve.md).

It is billable: one droplet + one Firecracker VM (kept Stopped after the
snapshot, so it can be re-baked, or terminated once the snapshot exists). Run on
the operator's turn:

    bench --site atlas.tests.local execute \
        atlas.tests.e2e.use_cases.bench_image.run

`run_smoke` reuses the shared bootstrapped droplet (cheap); `run` provisions a
brand-new server. Both leave the snapshot row + its LV in place — that is the
artifact. Teardown when done:

    bench --site atlas.tests.local execute \
        atlas.tests.e2e.use_cases.bench_image.teardown \
        --kwargs '{"virtual_machine": "<vm-name>"}'
"""

import frappe

from atlas.atlas import bench_image
from atlas.atlas._ssh.transport import run_ssh, ssh_key_file
from atlas.atlas.ssh import connection_for_guest
from atlas.tests.e2e._config import control_plane_public_key, ephemeral_public_key
from atlas.tests.e2e._droplets import phase
from atlas.tests.e2e._image import ensure_image_on_server
from atlas.tests.e2e._tasks import wait_for_vm_running

# The bake clones Frappe + builds a uv venv + Node deps; 4 GB is too tight, so
# the build VM (and therefore the snapshot, and clones from it) gets a roomier
# disk. The base ubuntu-24.04 image is 4 GB; provision-vm grows the per-VM rootfs
# on a larger disk_gigabytes (spec/08 "Per-VM rootfs creation" step 2).
GOLDEN_DISK_GB = 12
GOLDEN_MEMORY_MB = 2048


def run(reuse: bool = False, keep: bool = True) -> dict:
	"""Provision a NEW server (reuse=False), bake the golden bench image on it,
	snapshot it, and leave the snapshot in place. Returns a summary dict."""
	with phase("bench-image", reuse=reuse, keep=keep) as server:
		return _bake(server.name)


def run_smoke(reuse: bool = True, keep: bool = True) -> dict:
	"""Dev-loop slice: reuse the shared bootstrapped droplet and bake there.

	Same bake + snapshot + `bench --version` proof as `run`, but on the shared
	server so we don't pay a fresh provision. The build itself (apt + clone + uv +
	node) is the slow part either way; reusing the droplet is the only saving."""
	with phase("bench-image (smoke)", reuse=reuse, keep=keep) as server:
		return _bake(server.name)


def _bake(server_name: str) -> dict:
	image = ensure_image_on_server(server_name)
	print(f"[bench-image] base image on server: {image.name}")

	vm = _provision_build_vm(server_name, image.name)
	print(f"[bench-image] build VM: {vm.name}  v6={vm.ipv6_address}")

	# 1. Bake bench-cli + `bench init` inside the guest (slow: apt + clone + uv).
	print("[bench-image] building bench inside the guest (apt + clone + uv + node) ...")
	bench_image.build_bench(vm.name)

	# 2. The host fact the golden image exists to prove: bench actually works in the guest.
	version = _assert_bench_works(vm)
	print(f"[bench-image] bench responds in the guest: {version}")

	# 3. Stop + snapshot. Snapshot requires a Stopped VM (clean unmount → no torn
	#    ext4). The snapshot is the golden image: site VMs clone from it.
	vm.stop()
	vm.reload()
	assert vm.status == "Stopped", vm.status
	snapshot_name = vm.snapshot(title="golden-bench")
	print(f"[bench-image] snapshot (golden image): {snapshot_name}")

	summary = {
		"server": server_name,
		"build_vm": vm.name,
		"build_vm_ipv6": vm.ipv6_address,
		"snapshot": snapshot_name,
		"bench_version": version,
	}
	print("")
	print("=" * 64)
	print("GOLDEN BENCH IMAGE BAKED — snapshot LEFT IN PLACE (the artifact).")
	for key, value in summary.items():
		print(f"  {key:<16} {value}")
	print("")
	print("  Site VMs clone from it via Virtual Machine Snapshot.clone_to_new_vm.")
	print("  Tear down the build VM when done (the snapshot survives):")
	print(
		"    bench --site atlas.tests.local execute "
		"atlas.tests.e2e.use_cases.bench_image.teardown "
		f'--kwargs \'{{"virtual_machine": "{vm.name}"}}\''
	)
	print("=" * 64)
	return summary


def _provision_build_vm(server_name: str, image: str) -> "frappe.model.document.Document":
	# build_bench reaches the guest via connection_for_guest (the ATLAS-settings
	# key), and the host-side `bench --version` probe SSHes with the EPHEMERAL key,
	# so the build VM must trust BOTH (authorized_keys is one key per line) — the
	# same dual-key shape the proxy VM uses.
	authorized = ephemeral_public_key() + "\n" + control_plane_public_key()
	vm = frappe.get_doc(
		{
			"doctype": "Virtual Machine",
			"title": "golden bench — build",
			"server": server_name,
			"image": image,
			"vcpus": 2,
			"memory_megabytes": GOLDEN_MEMORY_MB,
			"disk_gigabytes": GOLDEN_DISK_GB,
			"ssh_public_key": authorized,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	wait_for_vm_running(vm.name, timeout_seconds=120)
	vm.reload()
	assert vm.status == "Running", vm.status
	return vm


def _assert_bench_works(vm) -> str:
	"""SSH into the guest and run `bench -b atlas list-apps` AS the frappe user
	through its login shell (install.sh put bench-cli on PATH in the frappe user's
	~/.bashrc, and the bench is baked under that user). Proves the bake survived
	unsquash→pack→provision→boot and bench is actually invokable, not just present
	on disk. The controller SSHes in as root, so drop to frappe with `sudo -u`."""
	connection = connection_for_guest(vm)
	with ssh_key_file(connection.ssh_private_key) as key_path:
		stdout, stderr, code = run_ssh(
			connection,
			key_path,
			# Drop to frappe through a login shell so ~/.bashrc's PATH is sourced.
			"sudo -u frappe bash -lc 'bench -b atlas list-apps'",
			timeout_seconds=120,
		)
	assert code == 0, f"bench did not run in the guest (exit {code}): {stderr[-500:]}"
	# list-apps prints the installed apps (frappe at minimum); assert frappe baked.
	assert "frappe" in stdout.lower(), f"frappe not found in baked bench: {stdout[-300:]}"
	return stdout.strip().splitlines()[-1] if stdout.strip() else "ok"


def teardown(virtual_machine: str) -> None:
	"""Terminate the build VM. The snapshot row + its LV survive (it is the
	golden image); delete the snapshot separately if rolling a new bake."""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	if vm.status != "Terminated":
		vm.terminate()
		frappe.db.commit()
		print(f"[teardown] terminated build VM {virtual_machine} (snapshot survives)")
