"""Unit tests for bootstrap-server.py's Atlas-venv step (ensure_atlas_env).
Stdlib-only — run with `python3 -m unittest atlas.test_bootstrap` from
scripts/lib. The host `run()` is mocked, so nothing actually shells out.

bootstrap-server.py is a hyphenated entry script (not importable as a module),
so it is loaded by path exactly the way the `atlas` CLI dispatcher loads the
other entries. The bootstrap carve-out keeps this script on the host's
/usr/bin/python3 (it CREATES the venv, so it cannot require it); these tests pin
that its uv step issues the right commands and that its DEEP sanity gate refuses
a half-built venv.
"""

import importlib.util
import os
import unittest
from unittest.mock import patch

# scripts/lib/atlas/test_bootstrap.py → scripts/bootstrap-server.py
_BOOTSTRAP = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
	"bootstrap-server.py",
)


def _load_bootstrap():
	spec = importlib.util.spec_from_file_location("_atlas_bootstrap_under_test", _BOOTSTRAP)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class TestEnsureAtlasEnv(unittest.TestCase):
	def setUp(self):
		self.bootstrap = _load_bootstrap()

	def _run_with_recorder(self, version_line):
		"""Run ensure_atlas_env with `run()` mocked. Returns (result, argv_calls).
		`--version` yields `version_line`; everything else returns ''."""
		calls = []

		def fake_run(*argv, **kwargs):
			calls.append(list(argv))
			if "--version" in argv:
				return version_line
			return ""

		with patch.object(self.bootstrap, "run", side_effect=fake_run):
			result = self.bootstrap.ensure_atlas_env()
		return result, calls

	def test_installs_uv_then_creates_venv_and_pip_installs_the_package(self):
		result, calls = self._run_with_recorder("Python 3.14.3\n")
		self.assertEqual(result, "Python 3.14.3")
		flat = [" ".join(argv) for argv in calls]

		# 1. uv installed at the PINNED version into the fixed, single-root dir.
		install = next(c for c in flat if "astral.sh/uv/" in c)
		self.assertIn(self.bootstrap.UV_VERSION, install)
		self.assertIn(f"UV_INSTALL_DIR={self.bootstrap.UV_DIR}", install)
		self.assertIn("UV_UNMANAGED_INSTALL=1", install)

		# 2. a venv created on the controlled CPython at the durable venv root.
		venv = next(c for c in flat if " venv " in f" {c} ")
		self.assertIn(f"--python {self.bootstrap.PY_VERSION}", venv)
		self.assertIn(self.bootstrap.ATLAS_VENV, venv)

		# 3. the atlas package pip-installed into the venv from the durable bin tree.
		pip = next(c for c in flat if "pip install" in c)
		self.assertIn(f"VIRTUAL_ENV={self.bootstrap.ATLAS_VENV}", pip)
		self.assertIn(self.bootstrap.BIN_DIRECTORY, pip)

	def test_exposes_the_console_script_on_path(self):
		_result, calls = self._run_with_recorder("Python 3.14.3\n")
		flat = [" ".join(argv) for argv in calls]
		# The generated console script is symlinked onto PATH at /usr/local/bin/atlas.
		link = next(c for c in flat if "ln -sfn" in c)
		self.assertIn(self.bootstrap.ATLAS_CLI, link)
		self.assertIn("/usr/local/bin/atlas", link)

	def test_deep_sanity_gate_exercises_lvm_import_hook_compile_and_cli(self):
		_result, calls = self._run_with_recorder("Python 3.14.3\n")
		flat = [" ".join(argv) for argv in calls]

		# (a) the atlas-pool.service inline import — the largest module, the
		#     likeliest stdlib gap on a fresh interpreter — run on the venv python.
		lvm = next(c for c in flat if "from atlas.lvm import ThinPool" in c)
		self.assertIn(self.bootstrap.ATLAS_PYTHON, lvm)

		# (b) all four firecracker-vm@.service boot hooks PARSE on the venv python.
		compile_call = next(c for c in flat if "py_compile" in c)
		for hook in ("vm-disk-up.py", "vm-network-up.py", "vm-network-down.py", "vm-restore.py"):
			self.assertIn(hook, compile_call)
		self.assertIn(self.bootstrap.ATLAS_PYTHON, compile_call)

		# (c) the `atlas` console script dispatches.
		cli = next(c for c in flat if c.startswith(self.bootstrap.ATLAS_CLI))
		self.assertIn("--help", cli)

	def test_version_mismatch_aborts_bootstrap(self):
		# A venv on the WRONG CPython must fail the bootstrap loudly (SystemExit) —
		# a unit pointing at a mismatched interpreter is never reached.
		with self.assertRaises(SystemExit):
			self._run_with_recorder("Python 3.12.7\n")


if __name__ == "__main__":
	unittest.main()
