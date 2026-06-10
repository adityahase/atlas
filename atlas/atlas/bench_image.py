"""Golden bench image control plane — bake a bench-preinstalled image by building
INSIDE a plain guest over SSH, then snapshotting it.

This is the controller side of plans/self-serve/01-golden-image.md, and the
direct sibling of `atlas.atlas.proxy.build_proxy`: provision a plain Ubuntu VM,
upload the committed `bench/` tree, run `build.sh` over the SAME SSH-to-the-guest
path the proxy build uses, then stop + snapshot the VM. That snapshot is the
reusable "golden bench image" — a VM with bench-cli, the uv venv, the Frappe
clone, MariaDB + Redis, AND a fully-created site baked under the fixed name
`site.local`, so `deploy-site.py` (plan 03) only RENAMES that baked site to the
per-VM FQDN (a directory move) + resets its admin password, never paying the
multi-minute `bench new-site` per signup.

We deliberately do NOT chroot-bake the rootfs at image-sync time: apt's
MariaDB/Redis postinst expect a running init, which a bare chroot lacks. Building
in a real booted guest (the proxy precedent) sidesteps that entirely and reuses
the existing Virtual Machine Snapshot machinery for the rollable artifact.

Each guest op is recorded as a Task row (synthetic `bench-build` script) for the
operator's audit trail, exactly like the proxy guest ops.
"""

import shlex
from pathlib import Path

import frappe

from atlas.atlas._ssh.transport import run_detached, run_scp, run_ssh, ssh_key_file
from atlas.atlas.proxy import _record_guest_task, _remote_parent
from atlas.atlas.ssh import connection_for_guest

# The committed bake tree ships in the repo's top-level `bench/` dir (beside
# `proxy/` and `scripts/`), not in the Python package. build_bench uploads it
# verbatim and runs build.sh — the same idiom as proxy.build_proxy. The `..`
# resolves the app symlink to the repo root.
REMOTE_BENCH_DIRECTORY = "/tmp/atlas-bench-build"


def _bench_source_directory() -> Path:
	return Path(frappe.get_app_path("atlas", "..")).resolve() / "bench"


def _bench_tree_uploads() -> list[tuple[Path, str]]:
	"""Every committed file under `bench/`, mapped to its remote path under
	REMOTE_BENCH_DIRECTORY, preserving the relative layout so build.sh finds its
	sibling bench.toml beside itself (it reads from its own directory)."""
	source = _bench_source_directory()
	uploads: list[tuple[Path, str]] = []
	for entry in sorted(source.rglob("*")):
		if not entry.is_file():
			continue
		relative = entry.relative_to(source)
		if "__pycache__" in relative.parts:
			continue
		uploads.append((entry, f"{REMOTE_BENCH_DIRECTORY}/{relative.as_posix()}"))
	return uploads


def build_bench(virtual_machine: str) -> None:
	"""Turn a freshly-provisioned Ubuntu guest into a golden bench: upload the
	committed `bench/` tree and run build.sh inside the guest (install bench-cli +
	`bench init` + bake a `site.local` site). After this returns the operator stops
	+ snapshots the VM; that snapshot is the rollable golden image (the
	build-in-guest + snapshot pattern, mirroring proxy.build_proxy →
	Virtual Machine.snapshot).

	Idempotent (build.sh re-runs cleanly: `bench init` is idempotent, the bench-cli
	clone is a fast-forward, and the baked site is skipped if already present), so
	this doubles as the "re-bake" verb.

	Recorded as a `bench-build` Task row for the audit trail, like every guest op.
	"""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	connection = connection_for_guest(vm)
	uploads = _bench_tree_uploads()
	with ssh_key_file(connection.ssh_private_key) as key_path:
		# Stage the whole tree under one dir so build.sh finds bench.toml beside
		# itself (it reads from the directory it lives in).
		remote_dirs = sorted({_remote_parent(remote) for _, remote in uploads})
		run_ssh(
			connection,
			key_path,
			"mkdir -p " + " ".join(shlex.quote(d) for d in remote_dirs),
			timeout_seconds=60,
		)
		for local, remote in uploads:
			run_scp(connection, key_path, str(local), remote, timeout_seconds=300)
		# Run the bake DETACHED, then poll — the bake is long (`bench init` apt-installs
		# MariaDB + Redis, builds the uv venv, clones + installs Frappe + Node, bakes a
		# site — 10-20 min), and a foreground SSH child dies if the connection resets
		# mid-build. The shared run_detached helper (transport.py) owns the
		# setsid+nohup + marker-poll mechanics; we just hand it `build.sh` and its
		# own log/done paths.
		stdout, stderr, code = run_detached(
			connection,
			key_path,
			f"chmod +x {REMOTE_BENCH_DIRECTORY}/build.sh && {REMOTE_BENCH_DIRECTORY}/build.sh",
			log_path=_BUILD_LOG,
			done_path=_BUILD_DONE,
		)
	_record_guest_task(virtual_machine, "bench-build", {}, stdout, stderr, code)
	if code != 0:
		frappe.throw(f"Bench build on {virtual_machine} failed (exit {code}): {stderr[-500:]}")


# Where the detached bake writes its log + completion marker on the guest.
_BUILD_LOG = f"{REMOTE_BENCH_DIRECTORY}/build.log"
_BUILD_DONE = f"{REMOTE_BENCH_DIRECTORY}/build.done"  # contains the exit code once finished
