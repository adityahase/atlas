"""Shared e2e helpers."""

import time
from datetime import datetime, timezone

import frappe

from atlas.atlas.digitalocean import DigitalOceanClient

TAG = "atlas-e2e"
SWEEP_AGE_SECONDS = 30 * 60


class MissingConfig(Exception):
	pass


def get_phase1_connection() -> dict:
	host = frappe.conf.get("atlas_phase1_host")
	key = frappe.conf.get("atlas_phase1_ssh_private_key")
	if not host or not key:
		raise MissingConfig(
			"Phase 1 e2e requires atlas_phase1_host and atlas_phase1_ssh_private_key "
			"in site config."
		)
	return {"host": host, "ssh_private_key": key, "user": "root"}


def get_client() -> DigitalOceanClient:
	token = frappe.conf.get("atlas_do_token")
	if not token:
		raise MissingConfig(
			"e2e needs atlas_do_token in site config: "
			"bench --site <site> set-config -p atlas_do_token <DO_TOKEN>"
		)
	return DigitalOceanClient(token=token)


def get_ssh_key_id() -> str:
	key_id = frappe.conf.get("atlas_ssh_key_id")
	if not key_id:
		raise MissingConfig("e2e needs atlas_ssh_key_id in site config")
	return key_id


def get_ssh_private_key() -> str:
	key = frappe.conf.get("atlas_ssh_private_key")
	if not key:
		raise MissingConfig("e2e needs atlas_ssh_private_key in site config")
	return key


def get_region() -> str:
	return frappe.conf.get("atlas_test_region", "blr1")


def get_size() -> str:
	return frappe.conf.get("atlas_test_size", "s-2vcpu-4gb-intel")


def get_image() -> str:
	return frappe.conf.get("atlas_test_image", "ubuntu-24-04-x64")


def sweep_old_droplets(client: DigitalOceanClient) -> None:
	"""Delete droplets tagged `atlas-e2e` older than SWEEP_AGE_SECONDS."""
	now = datetime.now(timezone.utc)
	for droplet in client.list_droplets_by_tag(TAG):
		created_at = droplet.get("created_at")
		if not created_at:
			continue
		try:
			created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
		except ValueError:
			continue
		age = (now - created).total_seconds()
		if age > SWEEP_AGE_SECONDS:
			print(f"sweeping leaked droplet {droplet['id']} ({droplet['name']}, age={int(age)}s)")
			try:
				client.delete_droplet(droplet["id"])
			except Exception as exception:
				print(f"  sweep failed: {exception}")


def create_test_droplet(client: DigitalOceanClient, name_suffix: str) -> dict:
	"""Create a tagged throwaway droplet and wait for it to be active."""
	name = f"atlas-e2e-{name_suffix}-{int(time.time())}"
	droplet = client.create_droplet(
		name=name,
		region=get_region(),
		size=get_size(),
		image=get_image(),
		ssh_key_ids=[get_ssh_key_id()],
		tags=[TAG, f"phase-{name_suffix}"],
		ipv6=True,
	)
	return client.wait_for_active(droplet["id"], timeout_seconds=300)


def cleanup_droplet(client: DigitalOceanClient, droplet_id: int) -> None:
	try:
		client.delete_droplet(droplet_id)
	except Exception as exception:
		print(f"cleanup failed for {droplet_id}: {exception}")
