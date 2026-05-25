"""Phase 2 e2e: real-droplet round trip against DigitalOcean."""

import time
import traceback

from atlas.atlas.digitalocean import public_ipv4, public_ipv6
from atlas.tests.e2e._shared import (
	cleanup_droplet,
	create_test_droplet,
	get_client,
	sweep_old_droplets,
)


def run() -> None:
	start_clock = time.monotonic()
	client = get_client()
	sweep_old_droplets(client)

	droplet = None
	try:
		droplet = create_test_droplet(client, "phase-2")
		assert droplet["status"] == "active"

		host_v4 = public_ipv4(droplet)
		host_v6, cidr_v6 = public_ipv6(droplet)
		print(f"created droplet {droplet['id']} v4={host_v4} v6={host_v6} prefix={cidr_v6}")

		fetched = client.get_droplet(droplet["id"])
		assert fetched["status"] == "active"

		client.delete_droplet(droplet["id"])
		_assert_gone(client, droplet["id"])
		droplet = None  # already deleted
	except Exception:
		elapsed = time.monotonic() - start_clock
		print(f"phase-2: FAIL in {elapsed:.0f}s")
		traceback.print_exc()
		raise
	finally:
		if droplet:
			cleanup_droplet(client, droplet["id"])

	elapsed = time.monotonic() - start_clock
	print(f"phase-2: OK in {elapsed:.0f}s")


def _assert_gone(client, droplet_id: int) -> None:
	deadline = time.monotonic() + 60
	while time.monotonic() < deadline:
		try:
			droplet = client.get_droplet(droplet_id)
		except Exception:
			return
		if droplet.get("status") in (None, "off", "archive"):
			return
		time.sleep(2)
	raise AssertionError(f"droplet {droplet_id} still present after 60s")
