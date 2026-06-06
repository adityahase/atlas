# Proxy work — session handoff

Read this, then [proxy-design.md](./proxy-design.md) (the full architecture) and
[ideas.md](./ideas.md) (the product goal: signup → dropped into a working bench).

## Where we are

The goal is a TLS-terminating reverse proxy that fronts many Frappe sites
(`*.<region>.frappe.dev`), so a signup lands the user on a working site over v4
**and** v6. The big architectural decision is **locked**: the proxy runs **inside
an Atlas VM** (the VM is the universal building block), not as a host service.

**Phase A (the inbound-v4 primitive) is DONE and committed** — `2653487 feat:
Attach reserved IPv4 to a VM via host anchor 1:1-NAT`. All four A-steps landed:
the spec (A.1), the provider abstraction (A.2), the host 1:1-NAT script (A.3),
and `attach()`/`detach()` wiring the Task (A.4). The e2e use case
(`reserved_ip_inbound.py`) proves both NAT halves on a live droplet — inbound
DNAT reached from off the droplet, egress SNAT via guest `cdn-cgi/trace`.

**Phase B (the proxy nginx stack) is DONE and proven — NOT yet committed.** The
`proxy/` tree exists and the docker-compose release gate is **10/10 green**
against a cleanly-rebuilt image (routing, remap-no-reload, branded-404, bulk
`/sync`, canonical-JSON byte-match, restart-reload-from-map.json, HTTP→HTTPS,
HTTP/2, socket.io upgrade). `build.sh` compiles the full stack — nginx 1.30.2 +
luajit2 + lua-nginx-module 0.10.29 + NDK + resty-core/lrucache + **lua-cjson** +
headers-more — and `nginx -t` passes. Three gotchas surfaced and were fixed
(none caught by compile alone, all caught by the compose harness — proof the gate
earns its keep):
- **lua-cjson is NOT bundled** with vanilla nginx (it ships in the OpenResty
  distro we deliberately don't use). persist/admin `require("cjson.safe")` →
  nginx crashes at `init_by_lua` without it. Now built from pinned source.
- **`lua-resty-core 0.1.33` was never cut as a stable tag** (only RCs); pinned
  the last stable `0.1.32`. (lua-cjson `2.1.0.14`, also verified at build.)
- **`error_page` does NOT intercept a Lua-phase `ngx.exit`.** The branded 404/503
  is now rendered *from router.lua* (read once, cached) with the right status;
  the router runs only in the two proxy locations (not server-wide) so its miss
  path can't re-enter itself (the named-location `ngx.exec` cycle is gone).

**Uncommitted state:** `proxy/` is untracked; `pytest` was pip-installed into the
bench venv for the harness. The big-picture docs (this file, proxy-design.md)
have unstaged edits. **The critical path now resumes at C (control plane: the
`Subdomain` doctype + guest-SSH + `proxy-sync.py`/`proxy-push-cert.py`).** Build
B has no host-bound proof yet — that's D (run `build.sh` in a real VM, snapshot,
prove inbound-:443 + map-sync over guest-SSH).

### Decisions locked (see proxy-design.md §1, §11)
- **Proxy is an ordinary operator-owned VM** — no infra-VM tier. Invisible to the
  user SPA because the SPA scopes by ownership.
- **2–3 proxy VMs per region** behind the wildcard (DNS round-robin), for
  resiliency + rolling rebuild updates.
- **A VM can attach one public IPv4** (Atlas-owned only, today). On DigitalOcean
  this is a **reserved IP** attached to the droplet, then **host 1:1-NATed** to
  the guest's private /30 — the **inbound mirror of the existing NAT44 egress**,
  same `inet atlas` nftables table. The proxy is its first user.
- **Atlas SSHes into the guest** (new target type: guest, not just host-root) to
  push the wildcard cert and sync the live map.
- Live map = `lua_shared_dict` in each proxy guest, reload-free; Atlas reconciles
  via SSH-to-guest + `curl --unix-socket`. (Unchanged from the nginx design.)

### Done this session (the Frappe-side foundation) — all tests green
- **Termination + stop protection** on `Virtual Machine`: two `Check` fields
  (default off), hard-throw gates in `stop()` and `terminate()`. Independent
  (terminate doesn't route through stop). 7 new tests.
- **Reserved IP DocType** (`atlas/atlas/doctype/reserved_ip/`) — standalone,
  linked to `Server` (the Snapshot idiom, not a child grid). Fields: `ip_address`
  (unique), `server`, `virtual_machine` (optional), `status`
  (Allocated/Attached, derived), `provider_resource_id` (DO reserved-IP id).
  `attach(vm)` / `detach()` enforce **one IP, one VM, same Server** and
  denormalize onto `VirtualMachine.public_ipv4`. 9 tests.
- `VirtualMachine.terminate()` now detaches its Reserved IP back to the pool.
- `VirtualMachine.public_ipv4` field (read-only, denormalized).
- Server Connections dashboard shows the Reserved IP pool under "Networking".
- Spec updated: `spec/02-doctypes.md` (Reserved IP section, new VM fields, count
  → twelve), `spec/05-virtual-machine-lifecycle.md` (protection gates, detach).

### NOT done — this is what "get the proxy working" still needs
Everything below is the gap between "Atlas can model an attachable v4" and "a
proxy VM actually serves TLS for a site." **The host-side wiring and the proxy
image/control-plane do not exist yet.**

## State / env notes
- Working in worktree `trees/bench` (branch `idea/bench`). The bench's
  `apps/atlas` symlink **currently points here** (`../trees/bench`). If `main`
  work resumes, repoint to `../trees/main` (operator's call).
- Test site: `atlas.tests.local`. Run unit tests with
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES bench --site atlas.tests.local
  run-tests --app atlas --module <mod>` from `/Users/aditya/Frappe/benches/v2`.
  `bench migrate` already applied (Reserved IP table + new VM columns exist).
- Uncommitted: the Reserved IP doctype is **untracked**; proxy-design.md,
  ideas.md, this file are untracked; the VM/spec/dashboard edits are unstaged.
  Nothing is committed yet — commit when the operator says so (CLAUDE.md: only
  commit your intended lines, `git add -p`; don't let ruff reformat old lines).

## TODO — sharp path to a working proxy

Ordered so each step is independently verifiable. Foundation first (the new
networking primitive), then the proxy image, then control plane, then e2e.

### A. The inbound-v4 primitive (the actual foundation everything rests on)
1. **[DONE] Spec the inbound half in `spec/06-networking.md`.** Added the
   `## IPv4 ingress (Reserved IP)` section (mirrors the NAT44 egress shape:
   provider-built vs host-not-built split) and softened the "No inbound IPv4"
   non-goal to "No inbound IPv4 **by default**" with the scoped exception.
   `spec/02-doctypes.md` got the new `allocate`/`discover`/`release` controller
   methods + the Release button.
2. **[DONE] Provider abstraction: DO reserved IP.** `Provider` ABC gained five
   reserved-IP methods (`allocate_reserved_ip` / `assign_reserved_ip` /
   `unassign_reserved_ip` / `list_reserved_ips` / `release_reserved_ip`) + a
   `ReservedIp` dataclass. `DigitalOceanClient` got the five `/reserved_ips`
   endpoints; `DigitalOceanProvider` implements all five (on DO the address IS
   the vendor handle). Self-Managed: `allocate` throws (operator supplies the
   address), the rest are no-ops/empty. `Reserved IP` doctype gained module
   functions `allocate(server)` (reserve new) + `discover(server)` (import
   existing, mapped by droplet id) and a `release()` method (explicit vendor
   destroy, like `Server.archive()`; `on_trash` only blocks deleting an attached
   IP and never touches the vendor). Fully unit-tested (client 24, DO provider
   12, self-managed 9, reserved-ip doctype 16 — all green). **Still NOT done in
   A.2 (deliberately deferred to A.3/A.4): no host NAT, and `attach()`/`detach()`
   do NOT yet call the provider to assign/unassign on the droplet — they own
   only the Frappe invariant.**
3. **[DONE] Host-side 1:1 NAT script.** `scripts/vm-reserved-ip.py` +
   `scripts/lib/atlas/reserved_ip_nat.py` add, idempotently in the `inet atlas`
   table, the `prerouting` DNAT and `postrouting` SNAT. **Key correction proven
   on a live droplet** (memory `atlas-reserved-ip-anchor-dnat`): DO delivers the
   reserved-IP packet with dest = the droplet's **anchor IP** (2nd eth0 addr,
   `10.47.x.x/16`, read from metadata `anchor_ipv4/address`), NOT the reserved
   IP — so the PREROUTING DNAT must match the **anchor**, else the packet hits
   the host's own sshd. Egress SNAT is unaffected. Rule generation unit-tested
   (`test_reserved_ip_nat.py`); the apply is the e2e host fact.
4. **[DONE] Wire `attach()`/`detach()` to run the Task.** attach → DO-assign +
   nft-up; detach → nft-down + DO-unassign. `vm-network-down.py` tears the NAT
   down on VM teardown; `provision-vm.py` re-applies on boot.

### B. The proxy nginx image

> **DECISION (this session): build in the VM, not a custom rootfs.** The
> operator chose **"SSH into a VM and build inside the VM."** This supersedes
> design §3.1 (docker-build → assemble-rootfs → register-image). The existing
> image model only downloads an upstream Ubuntu squashfs and converts it
> server-side — there is **no custom-rootfs path**, and self-hosting our own
> rootfs is exactly what spec principle #6 (don't build/store artifacts for the
> building block) avoids. So the proxy is built the Atlas-native way:
>
> 1. Provision an **ordinary Atlas VM from the stock Ubuntu image**.
> 2. Atlas **SSHes into the guest** (the same guest-SSH primitive phase C adds)
>    and runs `proxy/build.sh` *inside the guest* — compiles nginx+Lua, installs
>    `/opt/atlas-proxy`, the config, the three Lua modules, the guest unit.
> 3. **Snapshot** the built VM (existing snapshot machinery) → that snapshot is
>    the reusable "proxy image". Roll = clone-from-snapshot / rebuild.
>
> No `proxy/build.py`-makes-an-image, no `sync-image.py` change, no overlay URL,
> no `Virtual Machine Image` row for the proxy. Docker is used **only** for the
> local compose test harness (§9), never to ship a rootfs.

5. **[DONE] Scaffold `proxy/`** (source tree): `conf/nginx.conf` (§5) +
   `conf/mime.types`, the three Lua modules `router.lua` / `admin.lua` /
   `persist.lua` (§6), `html/not_found.html`, the guest `atlas-proxy.service` +
   `tmpfiles.d/atlas-proxy.conf` (§8).
6. **[DONE] `proxy/build.sh`** — the in-guest build. Idempotent (spec taste #14):
   compile nginx 1.30.2 + luajit2 + lua-nginx-module 0.10.29 + NDK 0.3.4 +
   resty-core 0.1.32 / lrucache 0.15 + **lua-cjson 2.1.0.14** + headers-more 0.39
   from pinned sources, install the stack + conf + Lua + unit, enable
   `atlas-proxy.service`, run `nginx -t`. Run over SSH-to-guest; the built VM is
   then snapshotted. Plain bash, so the `atlas-py314-except-trap` syntax concern
   doesn't apply; the real portability risk is apt package names (uses
   `build-essential libpcre2-dev zlib1g-dev libssl-dev`, all present on Ubuntu
   24.04). Verified compiling clean in Docker (arm64; the guest is amd64 — same
   path).
7. **[DONE] docker-compose test harness** (`proxy/test/`, §9) — proxy + two fake
   v6 upstreams (static ULA addrs); `test_proxy.py` drives the admin socket via
   `docker compose exec` (NOT a host bind-mount — macOS Docker's fakeowner FS
   rejects nginx's socket `chmod`; exec is also faithful to prod, where Atlas
   reaches the socket from inside the guest). **10/10 green.** Reuses the same
   `conf/` + `lua/` via `build.sh`, so green compose == the stack the VM runs.
   Image-level release gate; nothing installed on the host. (`pytest` was
   pip-installed into the bench venv.) TLS *grade* (A+) is the one §9 row not
   automated — needs `testssl.sh`/a real cert, so it's a D/manual check.

### C. Control plane (Atlas → guest)
8. **Guest SSH target.** Teach the SSH layer (`atlas/atlas/ssh.py`,
   `secrets.get_ssh_key_from_disk`) to target a guest (addr = VM /128, user
   `atlas`, key injected into the proxy image) in addition to host-root. Handle
   guest-unreachable as a recorded Task failure.
9. **`scripts/proxy-sync.py`** — SSH-to-guest + `curl --unix-socket … POST /sync`
   the full regional map (canonical `json.dumps(sort_keys=True, indent=2)`).
   Per-proxy reconcile loop (every proxy VM in the region gets the full map).
   Byte-equality diff before syncing. Unit-test the diff + serialization.
10. **`scripts/proxy-push-cert.py`** — SSH-to-guest, drop
    `fullchain.pem`/`privkey.pem`, reload nginx.
11. **Desired-state model — DECIDED: a `Subdomain` DocType.** One row per
    subdomain (`subdomain` unique, `virtual_machine` → the site VM, `address`
    denormalized v6, `region`, `active`). Standalone linked doctype (the
    Reserved IP / Snapshot idiom this codebase favors), full-word name. The
    region's desired map is `SELECT subdomain, address WHERE region=R AND
    active`; every proxy VM in the region gets that same full map. Mark a VM as a
    proxy + its region via fields on `Virtual Machine` (`is_proxy`, `region`).
    *Not* a `Proxy Mapping` name, *not* a child table on a `Proxy` doctype (a
    child table fights the "every proxy holds the whole regional map" model).

### D. Prove it end-to-end (host facts — Atlas e2e)
12. **New e2e use case** (proxy-design.md §9.2): attach a reserved IP and prove
    the v4 reaches the guest :443 (inbound DNAT) and egress is the reserved v4
    (SNAT) — the **inbound-v4 reachability probe**; the **inbound-:80 to a site
    from the proxy's vantage** probe (the §2.1 release gate, never tested);
    guest-SSH map sync; rolling rebuild of one proxy while others serve.
13. **`spec/12-proxy.md`** — write the proxy as a proper spec chapter (source of
    truth). Doesn't exist yet.

## Watch-outs
- **South side stays public-v6** (proxies are dedicated, not co-located with
  sites): a site's :80 is reachable by anyone on the v6 internet. The guest
  firewall must scope :80 to the proxies, and **inbound :80 has never been
  tested** — both are release gates (proxy-design.md §2.1).
- **DO reserved IP attaches to the droplet, not the guest** — that's why the host
  1:1-NAT exists. Don't try to bind it inside the VM.
- **Proxy VM is terminable like any VM** (accepted risk, mitigated by 2–3 + the
  new termination_protection flag — set it on proxy VMs).
- macOS worker fork crash: any worker lacking
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` will hang e2e provisioning. See the
  `atlas-macos-worker-fork-crash` memory.
