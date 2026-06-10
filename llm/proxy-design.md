# Atlas Reverse Proxy — Design

The proxy is built and folded into the spec ([`spec/12-proxy.md`](../spec/README.md)).
This file is not the contract; it carries only **(a)** the design rationale the
spec deliberately doesn't keep and **(b)** the work that is *not yet built*.

---

## Why these decisions (the interview record)

The structural decisions, kept here because the spec records *what* is true, not
*why these and not the alternatives*:

1. **The proxy runs inside an Atlas VM, not a host service.** An earlier draft
   ran it as a host-level service on a dedicated proxy *node* (its own exported
   rootfs, `RootDirectory=` chroot, systemd hardening drop-ins). Superseded: the
   VM is the universal building block, so the proxy inherits Atlas's lifecycle,
   jailer, cgroup, image/rebuild, and snapshot machinery for free. The VM **is**
   the sandbox; there is no bespoke hardening stack. (The old host-service
   `systemd/` + `install.py`/`update.py` were never built.)

2. **2–3 proxy VMs per region — dedicated, not co-located per host.** Drivers:
   resiliency, rollover, rolling update. The rejected alternative — co-locating a
   proxy with the sites it fronts to make the south hop host-local — would have
   retired the public-v6 caveat (§ "Accepted limitations" below) but lost the
   dedicated-fleet resiliency. We took the caveat.

3. **Inbound is the real goal — a VM can attach one public IPv4.** This is the
   inbound mirror of the existing egress NAT44, gated to Atlas-owned VMs today.
   On DO it is a reserved IP attached to the *droplet* and host-side 1:1-NATed to
   the guest (DNAT in, SNAT out, same `inet atlas` table) — *not* routed the way
   v6 is, because DO delivers the reserved IP via an **anchor IP** and never ARPs
   for the reserved IP on the link, so the v6 proxy-NDP + `/32`-route recipe has
   nothing to bind to. The proxy is the primitive's first user; general tenant
   inbound v4 is a deliberate later step.

4. **No infrastructure-VM tier.** The proxy holds the wildcard private key and
   terminates TLS for the region — a higher trust tier than a tenant site — but
   we deliberately do **not** model that as a new DocType. It is an ordinary
   operator-owned `Virtual Machine`, invisible to the user SPA by ownership.
   Accepted risk: it can be Terminated from Desk like any VM (mitigated by
   running 2–3; a terminate-guard is an additive follow-up if it bites).

5. **Atlas SSHes into the guest.** A second SSH target type (guest, reaching the
   VM's `/128`) alongside the existing host-root path, used for both map sync and
   cert push. The guest admin API is a unix socket only — SSH-to-the-guest is the
   only way to reach it; socket file perms are the gate. No agent on the guest.

6. **The map is bulk-declarative reconcile, not event sourcing.** Atlas is the
   source of truth; each proxy's dict is a cache. Both sides emit the *same*
   canonical JSON (sorted keys, 2-space indent), so "in sync?" is a byte compare.
   Per-entry PUT/DELETE exist for low-latency single changes; the periodic full
   `/sync` is the backstop.

### Accepted limitations (carried into the release gate, documented in spec)

- **The proxy→site south hop is over the public IPv6 internet** (proxies and
  sites are generally on different hosts; there is no private fabric). A site's
  `:80` is therefore reachable by anyone on the v6 internet, not just the proxy.
  Scoping that exposure is an active security gap, not just a deferral — see
  remaining-work #1. The proxy is path-agnostic, so a future private fabric (ULA
  `fc00::/7`) changes only the address in the map.
- **One reserved IP per host, for now** — the DO anchor is per-droplet, so the L3
  DNAT can't distinguish two reserved IPs on one host. Fine at one proxy VM per
  host; multi-reserved-IP is a later step.

---

## Remaining work (not yet built)

The proxy itself is done; these are the gaps around it, roughly in priority
order. #1 is a security gap.

1. **South-side firewall: scope site `:80` to the proxies.** A site's `:80` is
   reachable by **anyone** on the v6 internet today (proxies are dedicated, not
   co-located). The `proxy_vm` e2e proves the proxy *can* reach the site's `:80`;
   it does **not** prove only the proxies can. A per-VM guest firewall that scopes
   `:80` to the proxy addresses doesn't exist yet — it's a release gate and a
   security gap, not just a TODO. It must scope inbound `:80` to the proxy source
   addresses without dropping the proxy hop.

2. **Remove an unhealthy proxy from the wildcard.** `upsert_wildcard` already
   publishes round-robin A/AAAA over the regional proxy fleet, but there is no
   health signal that *withdraws* a record when a proxy is down — a dead proxy
   still takes 1/N of the traffic until an operator reconciles by hand.

3. **Schedule the reconcile loop.** `reconcile_proxy` / `reconcile_region` exist
   and run on demand, but the *periodic* diff (the backstop — re-`/sync` every
   proxy on a timer, so a rebuilt/drifted proxy self-heals without an operator
   action) is **not wired**: `scheduler_events` in [`atlas/hooks.py`](../atlas/hooks.py)
   carries only the daily cert-renewal job, not `reconcile_region`. Adding it is a
   then-trivial step.

4. **TLS grade (A+) is not automated.** The one image-gate row the compose harness
   can't assert (needs a real cert + `testssl.sh`/`sslyze`). The TLS layer now
   produces a real cert, so this is gradeable — just not yet wired.

5. **404-only vs 404/503 tombstones.** Shipping 404-only; the known-down `503`
   "site suspended/preparing" path (a tombstone value in the map) is a small
   additive follow-up for the signup UX.

6. **Proxy VM sizing.** The per-VM cgroup caps (`vcpus`, `memory_megabytes`) and
   `LimitNOFILE` for a proxy are at sensible defaults; tune once real load is
   observed.

7. **General tenant inbound v4.** The v4-attach primitive is gated to Atlas-owned
   VMs. Letting a dashboard user attach a v4 to their own VM is a deliberate later
   step.

8. **`ssl_certificate_by_lua` / per-subdomain custom-domain certs.** Confirmed to
   work in the self-assembled build; the hook is left in place but not built —
   one wildcard covers everything this iteration.

9. **A proxy terminate-guard.** Accepted risk today (a proxy can be Terminated
   like any VM, taking down the region front door; mitigated by running 2–3). A
   lightweight guard (tag / naming convention / confirm-dialog) is an additive
   follow-up if it bites — see also the `termination_protection` watch-out below.

---

## Watch-outs (still true, not yet enforced in code)

- **Set `termination_protection` on proxy VMs.** A proxy is a terminable VM like
  any other (accepted risk, mitigated by 2–3/region + the flag). The flag exists;
  setting it on proxies is operator discipline, not yet automated (this is the
  near-term mitigation for remaining-work #9).
- **macOS worker fork crash** still bites e2e provisioning — any worker lacking
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` hangs the run
  (`atlas-macos-worker-fork-crash` memory).
- **The `apps/atlas` symlink** points at this worktree (`trees/bench`, branch
  `idea/bench`). Repoint to `trees/main` if main work resumes (operator's call).
