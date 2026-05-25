# Networking

Each VM gets one public **IPv6** address. No IPv4 in the guest. No private
network. No overlay.

## Why IPv6-only

DigitalOcean assigns each droplet a **/64 IPv6 prefix** that is routed to the
droplet. That gives us 2^64 addresses we can hand out to VMs without paying
extra and without needing NAT. IPv4 from DO is per-droplet only — we'd have
to NAT or use floating IPs (one per VM, paid). For the building-block
iteration we sidestep the IPv4 question entirely.

A VM that needs to *reach* IPv4 services from the internet will go through a
future "egress" component. Out of scope here.

## Per-VM addressing

For each VM we allocate one IPv6 address from the node's /64. We use a simple
**sequential allocator** scoped to the node:

```
node_prefix = "2a03:b0c0:abcd:1234::"     # the /64 routed to the node
vm_address  = node_prefix + hex(n+2)      # n is 0-based VM index on the node
```

We reserve `::1` for the host's own use (already configured by DO on the host
interface) and start VM addresses at `::2`.

We do **not** use SLAAC or RA. Each VM gets a static address baked into its
boot args (see below). This keeps the host-side routing trivial and avoids
running an RA daemon.

Allocation is a server-side step:

1. `SELECT max(...)` of allocated suffixes on the node from `Virtual Machine`
   rows.
2. Next suffix = max + 1, or 2 if none allocated.
3. Persist to `Virtual Machine.ipv6_address` **before** any SSH.

This is a single-writer system; we serialize VM creation per node using a
short row-level lock on `Metal Node` (a `frappe.db.get_value(..., for_update=True)`
inside the allocation function).

## MAC

Stable, derived from `vm_name`:

```
mac = "06:00:" + ":".join(f"{b:02x}" for b in sha1(vm_name.encode()).digest()[:4])
```

`06` sets the locally-administered bit. Two VMs with the same name on
different nodes would collide — fine, since `vm_name` is globally unique
(primary key).

## TAP

`tap_device = "tap-" + slug(vm_name)[:11]` (Linux IFNAMSIZ is 16, but we keep
margin). Stored on the doc.

## Host configuration

Done once by bootstrap:

```
# /etc/sysctl.d/60-atlas.conf
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.default.forwarding = 1
net.ipv6.conf.all.proxy_ndp = 1
```

The `proxy_ndp` is critical: because each VM lives behind a `/126` on a tap
device, we need the host to **answer NDP** for the VM's address on the
upstream interface (`eth0`). Without proxy NDP, DO's upstream router doesn't
know how to reach the VM.

We also create one nftables table at bootstrap:

```
nft add table inet atlas
nft 'add chain inet atlas forward { type filter hook forward priority filter; policy accept; }'
```

No NAT chain — we don't NAT IPv6.

## Per-VM host setup (`atlas-vm-postup {vm}`)

Run by systemd `ExecStartPost`. Reads `/var/lib/atlas/vms/{vm}/network.env`:

```
TAP_DEV=tap-vm001
VM_IPV6=2a03:b0c0:abcd:1234::2
HOST_UPLINK=eth0
HOST_IPV6_GW=2a03:b0c0:abcd:1234::1
```

Does:

```bash
# Create the tap device (idempotent: del + add).
ip link del "$TAP_DEV" 2>/dev/null || true
ip tuntap add "$TAP_DEV" mode tap
ip link set "$TAP_DEV" up

# Address a /126 on the host side of the tap.
# We use a unique-local fe80:: link-local on the tap for routing only.
# Actually, the simplest scheme: assign the VM's /128 directly to the tap
# device on the host and rely on neighbour proxy on eth0.
ip -6 route add "$VM_IPV6/128" dev "$TAP_DEV"

# Tell the host to answer NDP for the VM's address on the uplink.
ip -6 neigh add proxy "$VM_IPV6" dev "$HOST_UPLINK"

# Allow forwarding for this VM both ways.
nft add rule inet atlas forward ip6 daddr "$VM_IPV6" oifname "$TAP_DEV" accept
nft add rule inet atlas forward ip6 saddr "$VM_IPV6" iifname "$TAP_DEV" accept
```

## Per-VM teardown (`atlas-vm-postdown {vm}`)

Symmetric:

```bash
ip -6 neigh del proxy "$VM_IPV6" dev "$HOST_UPLINK" 2>/dev/null || true
ip -6 route del "$VM_IPV6/128" dev "$TAP_DEV" 2>/dev/null || true
ip link del "$TAP_DEV" 2>/dev/null || true

# Best-effort delete the two nft rules. We look them up by handle.
for h in $(nft -a list chain inet atlas forward \
  | awk -v vm="$VM_IPV6" '$0 ~ vm {print $NF}'); do
    nft delete rule inet atlas forward handle "$h" || true
done
```

The `nft` cleanup is intentionally best-effort. If a rule leaks, it does no
harm (the VM is gone, traffic to its address is dropped by RPF anyway), and
the operator can `nft flush table inet atlas` and re-run `atlas-vm-postup`
on each running VM as a last resort.

## In the guest

We use the kernel command-line `ip=` parameter (see Firecracker network-setup
doc, "Advanced: Guest network configuration using kernel command line") so the
guest configures its IPv6 without needing iproute2 — except that the `ip=`
parameter only configures IPv4. For IPv6, we extend `boot_args` with:

```
console=ttyS0 reboot=k panic=1
```

…and rely on the rootfs's own `/etc/netplan` to come up with **SLAAC**. But
we're using static addressing, not SLAAC. Two options:

- **Option A (chosen):** drop a one-shot `systemd` unit into the rootfs at
  provision time (during the same `mount -o loop` step we use to inject the
  SSH key). The unit reads `/etc/atlas-vm.env` (also dropped in) and runs:
  ```bash
  ip -6 addr add "$VM_IPV6/128" dev eth0
  ip link set eth0 up
  ip -6 route add default via fe80::1 dev eth0
  echo "nameserver 2606:4700:4700::1111" > /etc/resolv.conf
  ```
  We use the host's link-local on the tap as the next hop — Linux assigns
  every interface a link-local automatically; we coerce ours to `fe80::1` by
  setting it on the tap after creation. (`ip -6 addr add fe80::1/64 dev "$TAP_DEV"`.)

- Option B: switch to a routed config + RA. Defer.

So `atlas-vm-postup` adds one more line:

```bash
ip -6 addr add fe80::1/64 dev "$TAP_DEV"
```

And the in-guest one-shot unit's content is **stable across all VMs**; only
`/etc/atlas-vm.env` is per-VM. That env file contains `VM_IPV6=...`.

This means the Ubuntu CI rootfs needs to be patched **once per VM creation**
with two files. We already do `mount -o loop` for the SSH key, so we add the
unit + env file in the same window. Patching is documented in [08-images.md](./08-images.md).

## Address tracking & uniqueness

A unique index on `Virtual Machine.ipv6_address` plus the per-node serialized
allocation gives us:

- No two VMs share an address (DB-enforced).
- Sequential, predictable allocation per node.
- Easy debugging: `vm-001` on `metal-blr1-01` is `prefix::2`, `vm-002` is `::3`, etc.

## What we are *not* doing

- No IPv4 in the guest at all. Reaching v4-only services is a future problem.
- No firewall rules per-VM beyond accept-in/accept-out. The guest is on the
  public internet.
- No DDoS mitigation. DO does some at the edge; that's it.
- No floating/reserved IPv6. If the VM dies, its address goes back to the pool.
