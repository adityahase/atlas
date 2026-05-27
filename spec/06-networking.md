# Networking

Each VM gets one public **IPv6** address. No IPv4 in the guest. No private
network. No overlay.

## Why IPv6 only

DigitalOcean assigns each droplet a /64 IPv6 prefix, which is enough to give
every conceivable VM a unique routable address with no NAT. IPv4 from DO is
per-droplet — to give each VM its own v4 we'd need NAT or paid floating IPs.
For the building block we sidestep the v4 question entirely. A future
"egress" layer can NAT64 from above Atlas.

## What the host actually gives us

This depends on the provider type.

### DigitalOcean

DO advertises a /64 to the droplet, but only a **/124 (16 addresses) is
usable** for onward routing — addresses outside that /124 are not reachable
through DO's network from elsewhere on the internet. This is a real-world
DO limit, not a Firecracker limit.

The routable /124 is the one **containing the droplet's own IPv6
address**, not the first /124 of the /64. For example, a droplet whose
public v6 is `2400:6180:100:d0:0:1:4ae1:d001` gets `…:d000/124` as the
usable range; addresses elsewhere in `2400:6180:100:d0::/64` are silently
dropped at DO's edge. The Python helper
[`carve_virtual_machine_range(host_address, prefix_cidr)`](../atlas/atlas/networking.py)
computes this for us at provision time.

So:

- `Server.ipv6_prefix` records the full /64 we got (informational).
- `Server.ipv6_virtual_machine_range` records the **/124** carved around
  the host address that we actually hand out from.
- VMs are addressed inside that /124.

Inside a /124 we have 16 addresses. The host uses one (typically `::1`),
which leaves 15 for VMs. That is enough for the size of droplet we're using
in this iteration (`s-2vcpu-4gb-intel` realistically fits 5–10 VMs anyway).
When we move to bigger metal, we will revisit the addressing scheme.

### Self-Managed

The operator tells Atlas, at provision time, exactly which prefix is
available for VM addresses. Atlas does not derive it and does not assume
any specific prefix length:

- `Server.ipv6_prefix` is informational — typically the full prefix
  routed to the host (e.g. a /64).
- `Server.ipv6_virtual_machine_range` is what Atlas actually allocates
  from. It can be a /124 (matching the DO model), a /96, an /80, a full
  /64, or anything else the operator's upstream has given them. The
  allocator below does not care about the length.

A Self-Managed host with an extra /64 routed to it lifts the 15-VM cap
that constrains DO droplets.

## Allocation

Sequential, scoped per server:

```
ipv6_virtual_machine_range = 2a03:b0c0:abcd:1234::/124
live allocations            = ::2, ::3, ::5      # ::4 was terminated earlier
next                        = ::4                # ::4 is back in the pool
```

`::1` is reserved for the host. We start at `::2`. The algorithm scans
existing `Virtual Machine.ipv6_address` rows for the server whose status is
not `Terminated`, and picks the lowest unused address inside
`ipv6_virtual_machine_range` (whatever its prefix length).

When the range fills up with live VMs, provisioning fails with "no IPv6
capacity". The operator either terminates old VMs (immediately releasing
their addresses) or provisions a new server. On a DigitalOcean /124 this
ceiling is 15; on a Self-Managed /64 it is effectively unbounded.

Terminated VMs release their address. The audit trail still lives in the
`Virtual Machine` row (status=Terminated, ipv6_address recorded at the
time it ran), so "which VM had this address on 2026-03-01?" is answered by
filtering on `creation`/`modified` — the field itself is not the index.

## MAC

Stable, derived from the UUID:

```
mac = "06:00:" + ":".join(format(b, "02x") for b in uuid.bytes[:4])
```

`06` sets the locally-administered bit. Two VMs would collide only if their
UUIDs share the first 4 bytes — practically impossible for UUID4.

## TAP device

`tap_device = "atlas-" + uuid_hex_no_dashes[:9]`. Linux `IFNAMSIZ` is 16
*bytes* including the null terminator, so usable interface-name length is
15: `atlas-` (6) + 9 = 15 exactly.

## Host-side configuration

Done once by `bootstrap-server.sh`:

```
# /etc/sysctl.d/60-atlas.conf
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.default.forwarding = 1
net.ipv6.conf.all.proxy_ndp = 1
```

`proxy_ndp` is the trick that makes the DigitalOcean scheme work. Each
VM has its address routed to a per-VM tap device, but DO's upstream
router asks NDP "who has 2a03:b0c0:abcd:1234::2?" on the uplink (`eth0`).
With proxy NDP enabled and an explicit `ip -6 neigh add proxy` entry on
the uplink for each VM address, the host answers on the VM's behalf.
The upstream router delivers to the host MAC; the host's route table
sends it out the right tap.

On Self-Managed hosts where the entire `ipv6_virtual_machine_range` is
**routed** to the host (not advertised on-link), the upstream router
already knows where to send those packets and proxy-NDP is a no-op.
`vm-network-up.sh` still adds the proxy-NDP entry — it costs nothing on
a routed prefix and keeps the script identical across providers.

We also create one nftables table (`inet atlas`) with one `forward` chain.
The table is **not** persisted to `/etc/nftables.conf`; instead
[`vm-network-up.sh`](../scripts/vm-network-up.sh) recreates it
idempotently at each unit-start and re-applies the IPv6 forwarding /
proxy-ndp sysctls defensively. This keeps each VM unit self-sufficient on
cold boot — after a host reboot, the first VM unit to start brings the
scaffold back. Per-VM forward rules are added by the same script.

## Per-VM, on the host

[`vm-network-up.sh`](../scripts/vm-network-up.sh), invoked by the systemd
unit's `ExecStartPre`, reads `network.env` and:

1. Creates a tap device for the VM with `vnet_hdr` enabled
   (`ip tuntap add … mode tap vnet_hdr`). Firecracker's virtio-net
   activation calls `TUNSETOFFLOAD` on the tap fd, which requires
   `IFF_VNET_HDR` on the device — without it activation fails with
   `EBADF` and the guest boots with no working NIC.
2. Assigns `fe80::1/64` to the tap (so the guest can use `fe80::1` as its
   gateway).
3. `ip -6 route add VM_IPV6/128 dev TAP_DEVICE`.
4. `ip -6 neigh add proxy VM_IPV6 dev <uplink>`.
5. Adds two nftables forward rules: ingress and egress.

It runs as `ExecStartPre`, not `ExecStartPost`: firecracker attaches to
the tap on startup, so the tap must exist with `vnet_hdr` before
firecracker's `ExecStart` fires. If `vm-network-up.sh` ran after, the
kernel would auto-create a tap without `vnet_hdr` when firecracker opens
it, then `vm-network-up.sh`'s idempotent `ip link del` would yank the
device out from under firecracker mid-boot.

[`vm-network-down.sh`](../scripts/vm-network-down.sh) is symmetric and
best-effort.

## Inside the guest

The Firecracker CI Ubuntu image is patched **at image sync time** (not at
VM provision time) with a single systemd unit,
[`scripts/guest/atlas-network.service`](../scripts/guest/atlas-network.service).
It reads `/etc/atlas-network.env` (which `provision-vm.sh` writes
per-VM containing `VIRTUAL_MACHINE_IPV6=...`) and runs:

```
ip link set eth0 up
ip -6 addr add ${VIRTUAL_MACHINE_IPV6}/128 dev eth0
ip -6 route add default via fe80::1 dev eth0
echo "nameserver 2606:4700:4700::1111" > /etc/resolv.conf
```

The guest does **not** use SLAAC or DHCPv6. Static addressing from
`/etc/atlas-network.env` keeps the host-side routing trivial and avoids
running an RA daemon on the host.

## Verifying connectivity

End-to-end check from any IPv6-capable client: `ping6
<VM_IPV6>`. If that fails, walk the stack from the outside in. Most
"VM is unreachable" reports map to one of these:

| Symptom                                 | Likely cause                                                  | Check                                                              |
| --------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------ |
| Host `…:d001` answers, VM `…:dXXX` does not | VM address is outside the routable /124                       | `Server.ipv6_virtual_machine_range` must *contain* the host address. If it starts at `…:d000` and the host is `…:d001`, good. If it starts at `:::/124` (the /64 start), the carve is wrong — see below. |
| VM address is in the /124, still silent | proxy-NDP entry missing on the uplink                         | On the host: `ip -6 neigh show proxy` should list the VM address against `eth0` (or whatever `ip -6 route show default` reports as `dev`). |
| Proxy entry present, still silent       | No host route into the tap                                    | On the host: `ip -6 route` should show `<VM_IPV6>/128 dev atlas-<...>`. |
| Route present, VM unreachable, guest can't ARP its gateway | Tap created without `vnet_hdr` (firecracker auto-created it before `vm-network-up.sh` ran) | On the host: `ip -d link show <tap>` — should list `tun … vnet_hdr on`. If absent, the VM's virtio-net activation failed silently and the guest came up with no working NIC. Cause: the script ran as `ExecStartPost` instead of `ExecStartPre`. |
| Tap looks right, ping still drops       | nftables forward rules missing                                | On the host: `nft list table inet atlas` should show one ingress + one egress rule per live VM. |
| Everything on the host looks right      | Guest didn't apply its address                                | In the guest console (firecracker log): look for `atlas-network.service` failures, or `ip -6 addr show eth0` showing no `<VM_IPV6>/128`. |

### Historical bug: the carve

Before [`atlas/atlas/networking.py`](../atlas/atlas/networking.py)
took `host_address` as well as `prefix_cidr`, the carve returned the
first /124 of the /64 (`2400:6180:100:d0::/124`). On a droplet whose
own v6 was `2400:6180:100:d0:0:1:4ae1:d001` the *routable* /124 is
`…:d000/124` — the carve was off by a wholly different sub-prefix
and VMs were assigned addresses DO silently dropped at its edge. The
host pinged fine (its own address was always routable); the VM was
opaque. The lesson: **the host's own address is the only datum that
tells you where DO put the routable window** — never derive the
/124 from the /64 alone.

### Historical bug: vnet_hdr

Before the systemd unit moved `vm-network-up.sh` to `ExecStartPre`,
firecracker's `ExecStart` won the race: it opened the tap fd first,
the kernel auto-created an `atlas-…` tap *without* `IFF_VNET_HDR`,
and firecracker's `TUNSETOFFLOAD` ioctl then failed with `EBADF`.
Firecracker logged a one-line warning and proceeded; the guest came
up with no working NIC. The fix in the unit is to create the tap
explicitly with `ip tuntap add … vnet_hdr` *before* firecracker
starts, which is why `vm-network-up.sh` is an `ExecStartPre` step
even though it touches host routing.

## What we do not do

- No IPv4 in the guest. Reaching v4-only services on the internet is a
  future problem.
- No per-VM firewall. The guest is on the public internet. Tightening this
  is on the [roadmap](./09-roadmap.md).
- No floating/reserved IPv6. If a VM is archived its address is retired.
- No DDoS mitigation. DO does what DO does at the edge.
