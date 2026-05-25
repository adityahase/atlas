"""Per-script sidecar uploads.

Some scripts need supporting files on the server before they run. The Server
bootstrap is special: its uploads are durable state (helper scripts + systemd
unit) placed by `Server.bootstrap()` directly, not through this map.

The map below is consulted by `ssh.py::_run_remote_script()` before each
script invocation. Paths in the value tuples are (local_relative_to_repo_root,
remote_absolute).
"""

SCRIPT_UPLOADS: dict[str, list[tuple[str, str]]] = {
	"sync-image.sh": [
		("scripts/guest/atlas-network.service", "/tmp/atlas/atlas-network.service"),
	],
}


def files_to_upload(script: str) -> list[tuple[str, str]]:
	return SCRIPT_UPLOADS.get(script, [])
