"""Central-facing inventory read for the Asset-mirror reconcile (spec/16-central.md).

Central pulls the authoritative VM list per Atlas to correct any drift the event
push missed. One row per tenant-tagged VM: its id, the owning team's
`central_reference`, status, and gateway_url. Operator-only (Central calls with
its service operator key); untenanted operator VMs are never returned.
"""

import frappe


@frappe.whitelist()
def tenant_vms(central_reference: str | None = None) -> list[dict]:
	"""Tenant-tagged VMs, optionally scoped to one team (`central_reference`)."""
	frappe.only_for("System Manager")

	if central_reference:
		tenants = frappe.get_all("Tenant", {"central_reference": central_reference}, pluck="name")
		if not tenants:
			return []
		vm_filter = {"tenant": ["in", tenants]}
	else:
		vm_filter = {"tenant": ["is", "set"]}

	vms = frappe.get_all(
		"Virtual Machine",
		filters=vm_filter,
		fields=[
			"name",
			"tenant",
			"title",
			"status",
			"vcpus",
			"memory_megabytes",
			"disk_gigabytes",
			"ipv6_address",
			"public_ipv4",
		],
	)
	refs = dict(
		frappe.get_all(
			"Tenant",
			filters={"name": ["in", [vm.tenant for vm in vms]]},
			fields=["name", "central_reference"],
			as_list=True,
		)
	)
	# Same shape as central_report._vm_payload so push and pull stay in lockstep.
	return [
		{
			"name": vm.name,
			"central_reference": refs.get(vm.tenant),
			"title": vm.title,
			"status": vm.status,
			"vcpus": vm.vcpus,
			"memory_megabytes": vm.memory_megabytes,
			"disk_gigabytes": vm.disk_gigabytes,
			"ipv6_address": vm.ipv6_address,
			"public_ipv4": vm.public_ipv4,
			"gateway_url": None,
		}
		for vm in vms
	]
