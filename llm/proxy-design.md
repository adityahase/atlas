# Atlas Reverse Proxy — Design

Status: **design, pending approval**. Nothing here is built yet. This document
is the contract; once approved it drives the scaffold (image build, nginx.conf,
Lua, guest unit, the v4-attach networking primitive, control-plane scripts,
compose test harness).

The current `llm/proxy.conf` is the **old, reload-based** model (Jinja → full
`nginx.conf` → `nginx -s reload` per change). This design replaces it: the
subdomain→VM map is **live and reload-free**.

> **Architecture change (this revision).** An earlier draft of this document
> ran the proxy as a **host-level service** on a dedicated proxy *node* (its own
> exported rootfs, `RootDirectory=` chroot, systemd hardening drop-ins). That
> draft is superseded. **The proxy now runs inside an ordinary Atlas Virtual
> Machine.** The VM is the universal building block — the proxy is just a VM
> like any other, and Atlas manages it through the lifecycle, jailer
> confinement, cgroup caps, image/rebuild, and snapshot machinery that already
> exist. Three decisions drove this (see §11):
> 1. **Inbound is the real goal** — a VM can now **attach one public IPv4**
>    (Atlas-owned VMs only, today). The proxy is the first VM to use it.
> 2. **No infrastructure-VM tier** — the proxy is an ordinary operator-owned VM.
>    It's invisible to the user SPA because the SPA scopes by ownership and the
>    operator isn't a dashboard user.
> 3. **Atlas SSHes into the guest** — Atlas gains a second SSH target type (the
>    guest, not just the host) to push the wildcard cert and sync the live map.
>
> The parts of the old design that were about *what nginx does* — the live
> `lua_shared_dict` map (§4), the static nginx config (§5), the three Lua
> modules (§6), the docker-compose test harness (§9) — survive unchanged,
> because moving nginx into a guest doesn't change them. The parts about
> *packaging and placement* (§3, §8) are rewritten: there is no exported rootfs
> and no host-service hardening stack — the VM **is** the sandbox.

---

## 1. What this is

A TLS-terminating reverse proxy that fronts many Frappe sites. Each site is a
subdomain of a regional wildcard (`*.<region>.frappe.dev`). Each subdomain maps
to exactly one VM (public IPv6, port 80, plaintext). The map changes constantly
(~10k/day) and must update **without reloading nginx**. Atlas (the Frappe app)
is the source of truth and reconciles the proxy's live map over SSH.

The proxy itself **is an Atlas Virtual Machine** — operator-owned, running the
self-built nginx+Lua stack inside the guest, with a **public IPv4 attached** so
it can terminate v4 *and* v6 on `:443`. A region runs **2–3 proxy VMs** for
resiliency, rollover, and zero-downtime updates.

### Decisions locked (from the design interviews)

| Question | Decision |
|---|---|
| Where the proxy runs | **Inside an Atlas Virtual Machine** (not a host service). Operator-owned, ordinary VM. The VM is the sandbox; Atlas's existing lifecycle/jailer/cgroup machinery confines it. |
| Proxies per region | **2–3 proxy VMs per region** — enough for resiliency, rollover, and rolling updates; not a per-host fan-out. All behind the one regional wildcard. |
| Inbound v4 | **A VM can attach one public IPv4** (Atlas-owned VMs only, today). On DigitalOcean this is a **reserved IP** attached to the droplet, then host-routed (1:1 NAT) to the specific VM. This is the new networking primitive; the proxy is its first user. |
| TLS cert delivery | **Atlas SSHes into the proxy guest** and drops the wildcard cert as a file (same SSH path as the map sync). No infra-tier secret store. |
| Proxy↔site topology | **Public IPv6.** Proxy VMs and site VMs are addressed by public `/128`; the proxy dials each site's `[addr]:80` over public v6 (proxy and sites are generally on *different* hosts, so there is no host-local shortcut). Traffic to the site is plaintext. The proxy is **path-agnostic** — it dials whatever address Atlas hands it. |
| Lua stack | **Self-assembled** vanilla nginx + OpenResty `luajit2` + `lua-nginx-module` (NOT the OpenResty distribution). |
| Live map storage | **`lua_shared_dict` is the source of truth in-process**; periodically dumped to **sorted, pretty-printed JSON** (`map.json`) inside the guest; nginx loads the file **only at start**. JSON is chosen for one-line diffs and `jq`/`grep`-ability. |
| TLS | **One regional wildcard** `*.<region>.frappe.dev`, acquired out-of-band, pushed into each proxy guest by Atlas. |
| Packaging | **A Virtual Machine Image** (the self-built nginx baked into the rootfs) provisioned and rolled like any other Atlas image. No exported rootfs, no host service, no container runtime. |
| Admin API | **Unix socket only** (no TCP), inside the guest. |
| Control plane | **Atlas owns the map**; SSHes **into each proxy guest** and talks to nginx **over the unix socket**; **periodically diffs** desired vs live and reconciles. |
| SSH→socket mechanism | **`curl --unix-socket` over SSH-to-the-guest**, declarative **bulk-sync** of the full desired map each reconcile. |
| Unmapped host | **Branded 404/503 page** served by nginx itself (no upstream). |
| Test harness | **docker-compose**: proxy + fake upstreams, drive the admin socket, assert routing/TLS/HTTP2/socket.io/remap/restart. (Tests the nginx image; the VM wrapping is exercised by the Atlas e2e suite.) |

### Non-goals (this iteration)

- No per-subdomain custom-domain certs / dynamic SNI (`ssl_certificate_by_lua`).
  One wildcard covers everything. Hooks left for later (§6.4).
- No multiple backends per subdomain, no load balancing, no health checks
  beyond "connect failed → 502". One subdomain → one VM address.
- No proxy-side caching of site HTML. (Asset caching is optional, §5.4.)
- No HTTP/3 / QUIC. HTTP/2 only.
- No shared map across proxy VMs. Each proxy VM is **independent and holds the
  full regional map**; Atlas reconciles each one. (The map is small; replicating
  it to 2–3 nodes is free.)
- **No general tenant inbound v4 yet.** The v4-attach primitive (§2.2) is gated
  to **Atlas-owned VMs**. Letting a dashboard user attach a v4 to their own VM
  is a deliberate later step, not built here.
- No infrastructure-VM DocType / trust tier. The proxy is an ordinary
  operator-owned `Virtual Machine` (§2.3).

---

## 2. Topology

```
                         Internet (v4 + v6)
                              │  :80 / :443  (v4 AND v6)
              ┌───────────────┼────────────────┐   DNS: 2–3 A + 2–3 AAAA
              ▼               ▼                 ▼   for *.<region>.frappe.dev
        ┌───────────┐  ┌───────────┐    ┌───────────┐
        │ Server H1 │  │ Server H2 │    │ Server H3 │   (Firecracker hosts)
        │           │  │           │    │           │
        │ reserved  │  │ reserved  │    │ reserved  │   ← DO reserved IP per host,
        │  v4 ─1:1─┐│  │  v4 ─1:1─┐│    │  v4 ─1:1─┐│     1:1-NAT'd to the proxy VM
        │  proxy VM││  │  proxy VM││    │  proxy VM││
        │ ┌────────▼┐│  │ ┌────────▼┐│    │ ┌────────▼┐│
        │ │ nginx + ││  │ │ nginx + ││    │ │ nginx + ││  ← proxy = a VM
        │ │ Lua     ││  │ │ Lua     ││    │ │ Lua     ││    (public v6 /128
        │ │ dict    ││  │ │ dict    ││    │ │ dict    ││     + attached v4)
        │ │admin.sock││  │ │admin.sock││    │ │admin.sock││
        │ └────┬─────┘│  │ └─────────┘│    │ └─────────┘│
        └──────┼──────┘  └────────────┘    └────────────┘
               │ proxy_pass http://[<site v6>]:80  (public IPv6, plaintext)
        ┌──────┼───────────────────┐
        ▼      ▼                    ▼
   [site VM] [site VM]  ...   [site VM]      tenant guests on any host
   (one or many subdomains may point at the same site VM)

   ▲ Atlas (Frappe, source of truth)
   │   ── SSH to HOST (root) ──► host Tasks (provision, network, jailer)
   └── SSH to GUEST (atlas user) ──► proxy VM:
         · push wildcard cert  (drop a file)
         · sync live map       (curl --unix-socket admin.sock /sync)
```

- **North side (public):** each proxy VM's nginx listens on `[::]:443` (its
  public `/128`) **and** `0.0.0.0:443` (its **attached IPv4**), HTTP/2, TLS, and
  `:80` (redirect only). One regional wildcard cert, pushed by Atlas. The
  region's DNS publishes the **2–3 proxy v6 + 2–3 proxy v4** addresses, so the
  wildcard resolves across the proxy fleet (DNS round-robin; a failed proxy is
  dropped from DNS by Atlas).
- **South side:** nginx `proxy_pass`es to `http://[<site-v6>]:80`, plaintext,
  over the **public IPv6 internet** (the only path between a proxy on one host
  and a site on another — see §2.1). **No TLS to the site.**
- **Map:** lives in `lua_shared_dict sites` *inside each proxy guest*. A
  request's `Host` → subdomain key → site address. Miss → branded 404.
- **Control:** Atlas SSHes **into the proxy guest** and `curl --unix-socket`s
  the admin API to sync the map, and drops the cert file. Periodic diff keeps
  live == desired, per proxy VM.

### 2.1 The proxy→site path: public IPv6

The proxy VMs (2–3 per region) and the site VMs they front are generally on
**different hosts**. There is no private fabric between hosts (`spec/06`: "No
private network between VMs, no overlay"), so the proxy dials each site's public
`/128` — exactly the `ping6 <VM_IPV6>` path the spec proves end-to-end. The map
value is just `[<v6 addr>]`; the proxy is **path-agnostic** and dials whatever
IPv6 literal Atlas hands it, so a future private fabric (ULA `fc00::/7`) changes
only the *address in the map*, never the proxy config.

**Security consequence (carried into the release gate):** on the public path, a
site's `:80` is reachable by **anyone** on the IPv6 internet, not just the
proxy. Two mitigations, both already foreseen by the `atlas-vm-inbound-ipv6-only`
memory:

- The guest's per-VM nftables forward chain (`inet atlas`) must keep an explicit
  `tcp dport 80` accept — and, ideally, **scope inbound :80 to the proxy VMs'
  source addresses** so only the proxies can reach the site, not the open
  internet. Today the forward chain is `policy accept` (no per-VM firewall yet);
  a future per-VM firewall must not silently drop the proxy hop.
- **Inbound TCP:80 from the proxy's vantage has never been tested** (the e2e
  proves outbound `curl` + inbound `ping6` only). Per the memory, an
  **inbound-:80 reachability probe is a release gate** for this proxy.

This is a known, accepted limitation of the public-path iteration, documented in
`spec/12-proxy.md`. (Co-locating a proxy with the sites it fronts — which would
make the south hop host-local and retire this caveat — was considered and
**rejected** in favor of 2–3 dedicated proxies per region for resiliency; see
§11, decision 2.)

### 2.2 The new primitive: attach a public IPv4 to a VM

Today Atlas VMs are **inbound-IPv6-only**: one public `/128`, IPv4 is
egress-only via a host-wide NAT44 masquerade, no inbound v4, no per-VM public v4
(`spec/06-networking.md`). The proxy needs inbound v4. So Atlas gains a new,
narrowly-scoped capability:

> **A Virtual Machine may have one public IPv4 attached. Atlas-owned VMs only,
> for now.**

This is the **inbound mirror of the existing egress NAT44**, and it lives in the
same `inet atlas` nftables table, recreated idempotently by the per-VM network
script (the same pattern as `vm-network-up.py`).

**On DigitalOcean** (the path built now): a public v4 is a **reserved IP**, a DO
API object. Atlas:

1. Allocates/attaches a DO reserved IP **to the droplet** (the host) — a
   reserved IP binds to a droplet, not to a Firecracker guest.
2. On the host, **1:1 NATs** that reserved IP to the proxy VM's private v4 (the
   guest side of its `/30`, the same address NAT44 already uses for egress):
   - **inbound:** `prerouting` DNAT — `ip daddr <reserved-v4> dnat to <guest-v4>`
   - **outbound:** `postrouting` SNAT — `ip saddr <guest-v4> snat to <reserved-v4>`
     (overriding the host-wide masquerade for this one guest, so its egress v4
     is the reserved IP, not the host's shared address).
3. The **guest contract is unchanged**: the guest still sees only its private
   `100.64.x.x/30` (exactly as for NAT44 egress today) and never knows it's
   behind NAT. nginx binds `0.0.0.0:443` on the guest's private v4; the world
   reaches it on the reserved v4. No guest-side v4 reconfiguration is needed
   beyond what `atlas-network.service` already does.

So "attach a v4 to a VM" on DO is **implemented as host-side 1:1 NAT from a
reserved IP to the guest /30** — symmetric with the egress masquerade, in the
same table, rebuilt idempotently at VM-network-up. The DocType change is one new
optional field on `Virtual Machine` (§2.3); the DO reserved-IP object is managed
through the provider abstraction like any other vendor resource.

**On Self-Managed**, the operator assigns a routable v4 directly; the host
routes it to the guest (no reserved-IP API). Out of scope to build now, but the
same `Virtual Machine` field carries it.

The full networking specification of this primitive lands in
`spec/06-networking.md` (the inbound half of the NAT story) — this section is
the proxy-facing summary.

### 2.3 The proxy is an ordinary VM (no infra tier)

The proxy holds the wildcard private key and terminates TLS for the whole
region — a higher trust tier than a tenant site. **We deliberately do not model
that as a new "infrastructure VM" DocType.** It is an ordinary
`Virtual Machine`, distinguished only by:

- **Owner = the operator** (not a dashboard Atlas User). The user SPA scopes
  every list by ownership (`spec/11-user-ui.md`, README principle #1), so an
  operator-owned VM never appears in any user's dashboard. This is why "reuse
  the tenant VM as-is" is safe — operator-owned VMs are *already* invisible to
  users; no new tier is required.
- **An attached IPv4** (§2.2) — the only structural difference from a tenant VM.
- **A proxy image** — the nginx stack baked into a `Virtual Machine Image`.

**Accepted risk, documented in the spec:** a proxy VM can be **Terminated** from
Desk like any other VM, and terminating it takes down the region's front door
for every site it fronts (mitigated by running 2–3). We accept this rather than
add a terminate-guard now — consistent with how Atlas documents accepted
limitations elsewhere. A lightweight guard (a tag, a naming convention, a
confirm-dialog) is an additive follow-up if it bites.

---

## 3. Packaging — build inside an Atlas VM, snapshot the result

There is **no exported rootfs, no host service, no container runtime on a node**
(the old §3 is gone). There is also **no custom `Virtual Machine Image`** for the
proxy: the existing image model only downloads an upstream Ubuntu squashfs and
converts it server-side (`spec/08-images.md`), and self-hosting a bespoke rootfs
is exactly what spec principle #6 (don't build/store artifacts for the building
block) avoids.

Instead the proxy is built **the Atlas-native way — inside an ordinary VM,
captured as a snapshot** (decided this session; supersedes the earlier
docker-build-a-rootfs draft):

```
1. Provision an ordinary Atlas VM from the STOCK Ubuntu image.
2. Atlas SSHes into the guest (the §7.3 guest-SSH primitive) and runs
   proxy/build.sh INSIDE the guest — compiles nginx+Lua from pinned sources,
   installs /opt/atlas-proxy + conf + the three Lua modules + the guest unit.
3. Snapshot the built VM (existing snapshot machinery, spec/05) → that snapshot
   IS the reusable "proxy image". Roll a new proxy = clone-from-snapshot.
```

Docker is used **only** for the local compose test harness (§9) — a fast,
host-free way to exercise the *same* `conf/` + `lua/` the in-guest build
installs — never to ship a rootfs to a server.

### 3.1 Build: compile nginx+Lua inside the guest (`proxy/build.sh`)

The self-built nginx + Lua stack (pinned versions below) is compiled and
installed **by `proxy/build.sh`, run over SSH inside a freshly-provisioned
Ubuntu VM**. The script is idempotent (spec taste #14: retry = re-run): it
`apt-get`s the build toolchain, fetches the pinned source tarballs, `./configure
&& make install`s nginx with the Lua modules, drops the committed `conf/` +
`lua/` + `html/` into `/opt/atlas-proxy`, installs the guest unit + tmpfiles, and
`systemctl enable`s `atlas-proxy.service`. The build tree compiles in Docker too
(the test harness, §9) so a contributor can iterate on the stack without a
droplet — but the **authoritative build target is the guest**.

Pinned versions (verified current, early 2026):

| Component | Version | Notes |
|---|---|---|
| nginx | **1.30.2** (stable) | highest core inside lua-nginx-module's tested ceiling |
| OpenResty `luajit2` | branch `v2.1-agentzh` (pinned dated tag) | **must** be OpenResty's fork, not upstream LuaJIT |
| `lua-nginx-module` | **0.10.29** | provides `lua_shared_dict`, `ngx.shared`, `ssl_certificate_by_lua` |
| `ngx_devel_kit` (NDK) | **0.3.4** | required, add **before** lua-nginx-module |
| `lua-resty-core` | **0.1.32** | **mandatory** — nginx refuses to start without it (0.1.33 was never cut as a stable tag — only RCs exist; 0.1.32 is the last stable, verified at build) |
| `lua-resty-lrucache` | **0.15** | dependency of lua-resty-core |
| `lua-cjson` | **2.1.0.14** | the `cjson` C module — **not bundled** with vanilla nginx (it ships in the OpenResty distro we don't use); persist/admin `require("cjson.safe")`, so nginx crashes at `init_by_lua` without it. Caught by the compose harness. |
| `headers-more-nginx-module` | **0.39** | `more_set_headers` (used by the old config) |

`./configure` (order matters — NDK before lua module):

```
LUAJIT_LIB=/usr/local/lib LUAJIT_INC=/usr/local/include/luajit-2.1 \
./configure \
  --prefix=/opt/atlas-proxy \
  --with-http_v2_module \
  --with-http_ssl_module \
  --with-ld-opt="-Wl,-rpath,/usr/local/lib" \
  --add-module=/build/ngx_devel_kit \
  --add-module=/build/lua-nginx-module \
  --add-module=/build/headers-more-nginx-module
```

Then `make install` the two pure-Lua resty libs into `/usr/local/share/lua/5.1`
(they are NOT compiled into nginx; nginx loads them at runtime). The `rpath`
flag is load-bearing — without it nginx can't find `libluajit-5.1.so` and won't
start.

### 3.2 In-guest layout

Inside the proxy guest's rootfs:

```
/opt/atlas-proxy/                 # the compiled stack (baked into the image)
  sbin/nginx
  lua/                            # router, admin, persist
  conf/nginx.conf                 # base config (the static parts, §5)
  html/                           # branded 404/503 pages
  ...luajit + resty libs...

/var/lib/atlas-proxy/
  map.json                        # the persisted map (§4.3) — written by nginx
  certs/<region>/                 # fullchain.pem, privkey.pem — pushed by Atlas
/var/log/atlas-proxy/
  access.log  error.log  admin.log
/run/atlas-proxy/
  admin.sock                      # unix socket for the admin API
  nginx.pid
```

These paths are inside the guest. To inspect state you SSH into the guest
(`atlas user`) and `cat /var/lib/atlas-proxy/map.json` /
`tail /var/log/atlas-proxy/error.log` — the same guest SSH path Atlas uses for
the map sync. There is no host bind-mount story anymore because there is no host
service; the VM boundary is the boundary.

### 3.3 nginx runs as a guest systemd unit

Inside the guest, nginx is a plain systemd unit (`atlas-proxy.service`) — the
guest's init manages it. The heavy host-service lockdown stack (the old §8:
`RootDirectory=` chroot, namespacing/syscall drop-ins) is **gone** — the
**Firecracker jail + per-VM netns + cgroup caps the host already applies to
every VM** are the sandbox. The proxy inherits Atlas's standard guest
confinement; it doesn't get a bespoke one. (See §8 for what remains: a minimal
guest unit, not a hardening framework.)

### 3.4 Install / update / roll = VM lifecycle

Rolling a new proxy build is **clone a fresh VM from the new snapshot**, not a
symlink-swap on a host:

- **Build** a new proxy snapshot: provision a VM from stock Ubuntu, run the new
  `proxy/build.sh` in it (new nginx/Lua/config), snapshot it.
- **Roll** the 2–3 proxy VMs one at a time: stand up a new proxy VM from the new
  snapshot (or **Rebuild** an existing proxy VM onto it), push its cert, let
  Atlas re-sync its map (the reconcile refills the dict on a fresh boot), verify
  it's serving, then the next, then the next. DNS keeps the others serving
  throughout → **zero-downtime rolling update**, which is exactly why we run 2–3.
- **Rollback** is clone/rebuild from the previous snapshot.
- **Snapshot** before a risky roll, using the existing snapshot machinery.

No `install.sh`/`update.sh` on a node; the "install/update/roll" verbs are
Atlas's existing VM lifecycle buttons (provision, snapshot, clone, rebuild). The
only proxy-specific control-plane scripts are the **build** (`proxy/build.sh`,
run once to bake a snapshot) and the **map sync / cert push** (§7).

---

## 4. The live map (reload-free)

*(Unchanged by the move into a VM — this is about what nginx does in-process.)*

### 4.1 Source of truth = `lua_shared_dict`

```nginx
lua_shared_dict sites 64m;   # subdomain -> VM address; 64m holds ~250k+ entries
```

The dict is the **authoritative in-process map**. Every request reads it; the
admin API writes it. No file is read on the request path. `set`/`get`/`delete`
on a shared dict are atomic and lock-free-ish — 10k writes/day is nothing
(shared dicts handle 100k+ ops/sec).

### 4.2 Request path (router Lua)

In a `map`-free, `access_by_lua`/`rewrite_by_lua` resolver:

```
host = ngx.var.host  (lowercased, port stripped)
subdomain = host without the ".<region>.frappe.dev" suffix
addr = ngx.shared.sites:get(subdomain)
if not addr then  -> internal redirect to @not_found (branded 404/503)
else              -> ngx.var.upstream = "http://[" .. addr .. "]:80"  (via set_by_lua / balancer)
```

We set the upstream with `set_by_lua` into an `nginx` variable and
`proxy_pass $upstream;` — or use `balancer_by_lua` for finer control. Either
keeps the address out of the static config entirely, so a map change is a pure
dict write, **no reload**.

### 4.3 Persistence (survive restart)

The dict is in shared memory; a restart (or a VM reboot/rebuild) wipes it. So:

- **Dump:** a timer (`ngx.timer.every`, or admin-triggered) serializes the dict
  to `/var/lib/atlas-proxy/map.json` **atomically** (write temp + `rename`).
  Triggered after writes (debounced) and periodically.
- **Load:** at worker init (`init_worker_by_lua` / `init_by_lua`) nginx reads
  the file and repopulates the dict. **The file is read only at start.**
- **Belt and suspenders across a rebuild:** even if `map.json` is lost (a fresh
  proxy image has none), the next Atlas reconcile (§7) bulk-`/sync`s the full
  desired map within one tick. The file is a fast-start cache; Atlas is the
  durable source of truth. This is what makes a proxy **Rebuild** safe.

**Format: sorted, pretty-printed JSON** — `{ "<subdomain>": "<address>" }`, keys
**sorted**, **`indent=2`**, one key per line, trailing newline:

```json
{
  "acme": "2400:6180:100:d0:0:1:4ae1:d002",
  "acme-test": "2400:6180:100:d0:0:1:4ae1:d002",
  "widgets": "2400:6180:100:d0:0:1:4ae1:d003"
}
```

Why sorted + pretty-printed (chosen for debuggability):
- **Stable, minimal diffs** — sorted keys + one-per-line means changing a single
  mapping is a **one-line diff**, not a reordered blob. The Atlas reconcile loop
  (and a human comparing desired vs live) reads the delta at a glance.
- **`grep`-able & readable in the guest** — SSH in, then `grep widgets map.json`,
  `jq . map.json`, or `cat` it.
- **One canonical serialization** — both sides (Lua dump + Atlas-side
  comparison) emit the *same* bytes for the same map (sorted keys, fixed
  indent), so a byte-equality check is a valid "in sync?" test. The reconcile's
  `desired != live` (§7.2) compares canonical JSON.
- **Self-describing & tool-friendly** — standard format, lua-cjson on the load
  path, `json` stdlib on the Atlas side. No bespoke parser.

Atomicity is preserved by writing a temp file + `rename` (§6.3) — the file is
always either the old or the new *complete* document, never a torn write.

Serialization details:
- **Lua dump:** lua-cjson does not guarantee key order, so `persist.lua` collects
  keys, `table.sort`s them, and emits the object in sorted order with 2-space
  indent. Deterministic bytes.
- **Atlas dump (for `/sync` body & comparison):** Python
  `json.dumps(map, sort_keys=True, indent=2)` — matches the Lua output
  byte-for-byte so the diff is meaningful.

### 4.4 Why this beats the old `map`-directive model

The old `proxy.conf` puts every site in an `nginx` `map{}` block — **static
config** that needs `nginx -s reload` to change, and a reload at 10k/day churn
means constant config regeneration + reload storms. The shared-dict model makes
a mapping change a single atomic memory write with **zero reload**.

---

## 5. nginx config (the static parts)

*(Unchanged by the move into a VM, except the v4 listener now binds the guest's
private v4 — the world reaches it via the attached reserved IP, §2.2.)*

These never change per-subdomain, so they live in the committed `nginx.conf`
(baked into the image) and only change on a deliberate config update (a new
proxy image + rolling rebuild, §3.4).

### 5.1 Listeners & HTTP/2

```nginx
# one server block handles ALL subdomains (wildcard cert, dynamic upstream)
server {
    listen 443 ssl default_server;        # the guest's private v4 (reserved IP 1:1-NATs to it)
    listen [::]:443 ssl default_server;   # the guest's public /128
    http2 on;                             # modern directive, not `listen ... http2`
    server_name ~^(?<subdomain>[^.]+)\.<region>\.frappe\.dev$;
    ...
}
```

IPv4 **and** IPv6 listeners. The v4 listener binds the guest's private
`100.64.x.x` (the world reaches it on the attached reserved IP via host 1:1 NAT,
§2.2 — nginx is unaware of the translation). HTTP/2 via `http2 on;` (the
`listen … http2` form is deprecated).

### 5.2 HTTP→HTTPS redirect + ACME passthrough

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # if certs are renewed via HTTP-01 on this node (optional; wildcard is DNS-01)
    location ^~ /.well-known/acme-challenge/ { root /var/lib/atlas-proxy/acme; }

    location / { return 308 https://$host$request_uri; }
}
```

(Wildcard certs are DNS-01, so the ACME passthrough is only there if a node ever
does HTTP-01 for a non-wildcard cert — harmless to keep.)

### 5.3 TLS for A+ (2026-current)

**Updated from the stale `proxy.conf`** per current best practice:

```nginx
ssl_certificate     /var/lib/atlas-proxy/certs/<region>/fullchain.pem;
ssl_certificate_key /var/lib/atlas-proxy/certs/<region>/privkey.pem;

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers off;
ssl_ecdh_curve X25519:prime256v1:secp384r1;     # or leave default `auto`

ssl_session_timeout 1d;
ssl_session_cache shared:MozSSL:10m;
ssl_session_tickets off;

add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
```

The cert/key are **pushed into the guest by Atlas over SSH** (§7), not baked
into the image (so the same proxy image serves any region and a renewed cert is
a re-push, not a rebuild).

**Two deliberate changes vs the old config:**

1. **Dropped the two `DHE-RSA-*` ciphers.** Mozilla's v6.0 Intermediate profile
   (2026) removed DHE. A+ unaffected; no `ssl_dhparam` needed.
2. **Removed `ssl_stapling` / `ssl_stapling_verify` / `ssl_trusted_certificate`.**
   Let's Encrypt shut down its OCSP responder (Aug 2025) and stopped embedding
   OCSP URLs (May 2025), so stapling is a no-op for LE certs. SSLLabs does not
   require it for A+.

A+ requirements met: TLS 1.2/1.3 only, strong ECDHE-GCM/ChaCha20 suites, HSTS
≥ 6 months (we use 2 years) with `includeSubDomains`. The other security headers
(`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`) are kept as
defense-in-depth.

### 5.4 Location blocks (per the request)

```nginx
# resolve subdomain -> upstream once per request
set $vm_upstream "";
access_by_lua_file /opt/atlas-proxy/lua/router.lua;   # sets $vm_upstream or 404s

location /socket.io {                 # SocketIO — websocket upgrade
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;         # long-lived ws
    proxy_pass $vm_upstream;
}

location / {                          # everything else
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_read_timeout 600s;
    proxy_buffering off;
    proxy_pass $vm_upstream;
}

location @not_found {                 # branded unmapped-host page
    internal;
    # 404 unknown subdomain, 503 known-but-suspended (router decides via status)
    root /opt/atlas-proxy/html;
    try_files /not_found.html =404;
}
```

`/socket.io` gets the WebSocket `Upgrade`/`Connection: upgrade` headers and a
long read timeout; the catch-all `/` gets buffering off (Frappe streams). Asset
caching (`/assets/` `proxy_cache`) is **optional** and can be added back — not
required for correctness.

---

## 6. Lua modules

*(Unchanged by the move into a VM.)*

Three small Lua files (matching the repo's "small files, OO where it earns it"
taste). Each module is a single-responsibility table.

### 6.1 `router.lua` (request path)

- Extract `host`, strip port, lowercase.
- Strip the `.<region>.frappe.dev` suffix → `subdomain` (config-injected region).
- `ngx.shared.sites:get(subdomain)`:
  - **hit** → `ngx.var.vm_upstream = "http://[" .. addr .. "]:80"`.
  - **miss** → `ngx.exec("@not_found")` (or set status 503 for a tombstoned
    entry — see §6.5).
- Hot path: one dict read, no allocation beyond the host parse. Sub-microsecond.

### 6.2 `admin.lua` (control path, unix-socket server only)

A separate `server{}` listening **only** on the unix socket:

```nginx
server {
    listen unix:/run/atlas-proxy/admin.sock;
    # no TCP, ever. File perms on the socket are the gate.
    access_log /var/log/atlas-proxy/admin.log;
    location / { content_by_lua_file /opt/atlas-proxy/lua/admin.lua; }
}
```

Routes (all write the shared dict, then schedule a debounced dump):

| Method & path | Effect |
|---|---|
| `GET /map` | dump entire dict as sorted pretty JSON (for the diff) |
| `GET /map/<sub>` | one entry (or 404) |
| `PUT /map/<sub>` body=`addr` | upsert one mapping |
| `DELETE /map/<sub>` | remove one mapping |
| `POST /sync` body=full map | **bulk declarative sync** — replace dict to match body atomically (the primary path, §7) |
| `POST /dump` | force an immediate persist to disk |
| `GET /healthz` | nginx up + dict entry count + last-dump time |

Auth: **socket file permissions** (owned by the control-plane user/group,
`0660`) inside the guest. The only thing that can reach the socket is a process
in the guest — and the only way Atlas gets there is SSH-to-the-guest. No token
(the locked decision is "unix socket only").

### 6.3 `persist.lua` (dump/load)

- `dump()` — `ngx.shared.sites:get_keys(0)`, `table.sort` the keys, emit a
  pretty-printed JSON object (`indent=2`, sorted keys, trailing newline) to a
  temp file, `os.rename` over `map.json`. Atomic; deterministic bytes (matches
  the Atlas-side `json.dumps(sort_keys=True, indent=2)`).
- `load()` — called from `init_worker_by_lua`; `cjson.decode` the whole
  `map.json`, `ngx.shared.sites:set(sub, addr)` for each. Only at start.
- A debounce flag + `ngx.timer.at` so a burst of writes coalesces into one dump.

### 6.4 Deferred: `ssl_certificate_by_lua` (custom-domain certs)

Confirmed to work in a self-assembled build (lua-nginx-module ≥ 0.10.21 +
lua-resty-core's `ngx.ssl`). **Not built this iteration** (one wildcard only),
but the hook is: an `ssl_certificate_by_lua_block` that looks up a per-SNI cert
in a second shared dict / on-disk cert dir, falling back to the wildcard.

### 6.5 Known-down vs unknown (404 vs 503)

To serve **503 "site suspended/preparing"** vs **404 "no such site"**, the map
value can carry a status: a tombstone value (e.g. `addr == "-"`) means "known
subdomain, intentionally down" → 503; absent key → 404. Keeps the branded-page
UX meaningful for the signup flow. Minor; can ship as plain 404-only first.

---

## 7. Control plane (Atlas-side)

Atlas is the source of truth; each proxy VM's dict is a cache. Reconciliation,
not event-sourcing — consistent with spec principle #2 ("the Frappe site is the
source of truth; a server is a cache").

### 7.1 Desired state in Atlas

A **`Subdomain` DocType** (decided this session — standalone, the Reserved IP /
Snapshot idiom; *not* a `Proxy Mapping` and *not* a child table on a `Proxy`
doctype, which would fight the "every proxy holds the whole map" model) holding
`subdomain (unique) → virtual_machine (the site VM) → address (denormalized v6)`,
plus `region` and `active`. A region has a set of **proxy VMs** (the 2–3
operator-owned VMs marked `is_proxy` with a `region` and an attached v4). The
desired map for the region is `SELECT subdomain, address WHERE region = R AND
active` — and **every proxy VM in the region gets the same full map** (§1
non-goals: each proxy holds the whole regional map).

### 7.2 Reconcile loop (the periodic diff)

A scheduled job **per proxy VM** (matching the "periodically checks the diff"
decision). The transport target is now the **guest**, not a host:

```
desired = atlas_db.map_for_region(region)                    # {sub: addr}
desired_json = json.dumps(desired, sort_keys=True, indent=2) # canonical bytes
for proxy_vm in region.proxy_vms:
    live_json = ssh_guest(proxy_vm, "curl --unix-socket /run/atlas-proxy/admin.sock GET /map")
    if desired_json != live_json:                            # byte-equality on canonical JSON
        ssh_guest(proxy_vm, "curl --unix-socket … -X POST /sync --data-binary @-", body=desired_json)
    record Task row (one task, one script — spec principle #3)
```

Because both sides emit the *same* canonical serialization, the in-sync check is
a plain string compare — no semantic diff needed.

- **Bulk declarative `/sync`** is the primary path: ship the full desired map,
  let each proxy replace its dict atomically. Idempotent, self-healing,
  rebuild-safe — if a proxy was rebuilt and its dict is empty, the next
  reconcile refills it. Same reconcile philosophy as `vm-network-up.py`
  recreating the nft scaffold idempotently.
- **Per-entry PUT/DELETE** exist for low-latency single changes (a new signup
  shouldn't wait for the next reconcile tick), fanned out to all proxy VMs in
  the region — but the periodic full `/sync` per proxy is the backstop.

### 7.3 Transport: SSH-to-the-guest + `curl --unix-socket`

This is the **new edge**: Atlas already SSHes to **hosts** as root to run Tasks.
For the proxy it SSHes **into the guest** (a dedicated `atlas` user, its own
key) and runs **one** command —
`curl --unix-socket /run/atlas-proxy/admin.sock -X POST http://localhost/sync
--data-binary @- < desired.json` — captures the result, records a Task. No agent
on the guest; nginx is the only daemon. Fits principle #5 (`curl` + `ssh`, both
present) and #3 (one task, one script).

What's genuinely new and must be specified/tested:

- **A guest SSH target.** Atlas's SSH layer (`secrets.get_ssh_key_from_disk`,
  the connection abstraction in `04-tasks.md`) must learn to target a guest
  (address = the proxy VM's `/128`, user = `atlas`, a key injected into the
  proxy image / pushed at provision) in addition to a host (root). This is the
  same SSH primitive pointed at a new endpoint, not a new transport.
- **Guest-reachability failure modes.** A guest can be Stopped, mid-boot, or
  wedged. The reconcile must treat "can't reach proxy guest" as a recorded Task
  failure and move on (the other proxies still serve), not wedge the loop.
- **Cert push.** The wildcard cert/key reaches the guest by the **same SSH
  path**: Atlas drops `fullchain.pem`/`privkey.pem` into
  `/var/lib/atlas-proxy/certs/<region>/` and triggers an nginx reload (a reload
  is fine here — cert changes are rare, unlike map changes). One task, one
  script (`proxy-push-cert.py`).

The map-sync script is the typed-Python idiom (`proxy-sync.py --proxy-vm … --map
…` emitting `ATLAS_RESULT=` JSON), consistent with the existing `scripts/*.py`.

---

## 8. Guest unit & confinement

The old host-service hardening framework (exported rootfs `RootDirectory=`
chroot, namespacing/syscall/cgroup drop-ins) is **deleted**. **The Firecracker
VM is the sandbox.** Atlas already jails every VM (per-VM uid/gid, chroot via
`jailer`, per-VM netns, cgroup-v2 memory/CPU caps — README non-goals,
`spec/05`). The proxy gets that for free and adds nothing bespoke.

What remains is a **minimal guest systemd unit** inside the proxy image:

```ini
[Unit]
Description=Atlas Reverse Proxy (nginx + Lua)
After=network-online.target atlas-network.service
Wants=network-online.target

[Service]
Type=forking
PIDFile=/run/atlas-proxy/nginx.pid
ExecStartPre=/opt/atlas-proxy/sbin/nginx -t -c /opt/atlas-proxy/conf/nginx.conf
ExecStart=/opt/atlas-proxy/sbin/nginx -c /opt/atlas-proxy/conf/nginx.conf
ExecReload=/opt/atlas-proxy/sbin/nginx -s reload
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Notes:

- **`MemoryDenyWriteExecute` is moot.** It was the one deliberate hardening
  exception in the old host-service design (LuaJIT JITs machine code at runtime,
  needs W^X). Inside the VM, the guest kernel + the per-VM cgroup/jail are the
  boundary; we don't impose a syscall filter on nginx within its own guest, so
  the JIT just runs. The host's jailer/seccomp posture (one layer out) is
  unchanged and applies to the whole Firecracker process.
- **Resource caps** are the VM's `vcpus` / `memory_megabytes` (the per-VM
  cgroup-v2 caps Atlas already sets), tuned by sizing the proxy VM — not a
  `30-resources.conf` drop-in. `LimitNOFILE` (many upstream conns) is set in the
  unit and should be generous (e.g. `1048576`).
- The admin socket is created by nginx (`listen unix:`) with a `tmpfiles.d`
  entry fixing `/run/atlas-proxy` perms — one fewer moving part.

---

## 9. Test harness (docker-compose, nothing installed on host)

*(Unchanged: this tests the **nginx image's behavior**. The VM-wrapping — image
sync, boot, v4-attach NAT, guest SSH, reconcile — is exercised by the Atlas e2e
suite as a new use case, §9.2.)*

A `docker-compose.yml` under `proxy/test/` brings up:

- **`proxy`** — the built nginx (the same stack baked into the VM image), with a
  self-signed `*.test.local` wildcard cert and a test region, admin socket
  bind-mounted out.
- **`vm-a`, `vm-b`** — tiny fake upstreams serving `/` (returns name + `Host`
  seen), `/assets/x` (static), `/socket.io` (echoes a websocket upgrade).
  Listen on `[::]:80` (v6) to mirror the real public-v6 target.

A pytest driver (run in a throwaway container or via the host's Python — no
nginx installed on the dev machine) asserts:

| Test | Asserts |
|---|---|
| **routing** | `PUT /map/acme = vm-a`, request `https://acme.test.local` → reaches vm-a, `Host` preserved |
| **remap (no reload)** | repoint `acme` → vm-b via admin socket, next request hits vm-b, **nginx never reloaded** (pid unchanged, no reload in error.log) |
| **multi-sub one VM** | `acme` and `widgets` both → vm-a, both work |
| **unmapped** | `https://nope.test.local` → branded 404 page, no upstream contacted |
| **bulk /sync** | POST full map, dict matches exactly (added + removed) atomically |
| **restart reload** | restart container, dict repopulates from `map.json`, routing still works (no admin calls) |
| **HTTP→HTTPS** | `http://acme.test.local` → 308 to https |
| **HTTP/2** | `curl --http2` negotiates h2 |
| **socket.io** | websocket upgrade through `/socket.io` succeeds, stays open |
| **TLS grade** | `testssl.sh` / `sslyze` against the container → A+-equivalent |

### 9.1 Testing multiple configs

The harness parametrizes the proxy container over a **config matrix** (regions,
dict sizes, with/without asset caching, v4-only vs v6-only listeners, empty vs
pre-seeded `map.json`); each variant gets `nginx -t` validated and the
behavioral suite run. A `configs/` dir holds the variants. This is the
**image-level release gate**.

### 9.2 New Atlas e2e use case: the proxy VM end-to-end

Because the proxy is now a VM, the **host-bound facts** belong in the Atlas e2e
suite (`spec/README.md` "Host facts vs unit-covered logic"), as a new use case
module mirroring the proxy lifecycle. These prove what only a real droplet can:

- **v4-attach (the new primitive):** attach a DO reserved IP to a host, 1:1-NAT
  it to a proxy VM, and prove the reserved v4 reaches the guest's `:443`
  (inbound DNAT) and the guest egresses as the reserved v4 (SNAT). This is the
  **inbound-v4 reachability probe**, the analog of today's egress-only e2e.
- **inbound-:80 to a site from the proxy's vantage** — the §2.1 release gate
  that has never been tested.
- **guest SSH** — Atlas SSHes into the proxy guest, syncs the map, reads it back.
- **rolling rebuild** — rebuild one proxy from a new image, re-push cert,
  re-sync map, confirm it serves while the others stay up.

Unit-coverable logic (the canonical JSON serialization round-trip, the reconcile
diff, the reserved-IP host/guest NAT math) gets `test_*.py` unit tests in
milliseconds, per the spec's host-facts-vs-logic split.

---

## 10. File layout (what the scaffold will add)

```
proxy/                              # the nginx stack source + its compose tests
  conf/
    nginx.conf                      # static config (§5)
    mime.types
  lua/
    router.lua                      # request path (§6.1)
    admin.lua                       # admin API over unix socket (§6.2)
    persist.lua                     # dump/load (§6.3)
  html/
    not_found.html                  # branded 404/503 (§5.4)
  guest/
    atlas-proxy.service             # minimal guest unit (§8)
    tmpfiles.d/atlas-proxy.conf     # /run/atlas-proxy perms
  build.sh                          # compile nginx+Lua INSIDE the guest, install
                                    #   the stack (§3.1); run over SSH, then snapshot
  test/
    Dockerfile                      # reuses build.sh to compile the SAME stack
    docker-compose.yml              # proxy + fake v6 upstreams (§9)
    test_proxy.py                   # pytest driver (§9)
  README.md                         # build/roll/test runbook

scripts/                            # Atlas-side control plane (existing dir)
  proxy-sync.py                     # ssh-to-guest + curl --unix-socket /sync
  proxy-push-cert.py                # ssh-to-guest + drop cert + reload

spec/
  06-networking.md                  # + the inbound v4-attach primitive (§2.2) [DONE]
  12-proxy.md                       # NEW: the proxy as source-of-truth spec
```

There is **no `Dockerfile` at the proxy root and no `build.py`** — the build is
`build.sh` run in the guest (§3.1); Docker exists only under `test/` for the
compose harness, where it invokes the *same* `build.sh` so the tested stack and
the shipped stack are byte-identical. The host-service `systemd/`, `install.py`,
`update.py` from the old design are **removed** — install/update/roll is VM
lifecycle (§3.4).

---

## 11. Decisions & remaining minor questions

### Decided (from the design interviews)

1. **The proxy runs inside an Atlas VM** — not a host service. The VM is the
   universal building block; the proxy inherits Atlas's lifecycle, jailer,
   cgroup, and image/rebuild machinery. (Supersedes the old exported-rootfs +
   host-service-hardening design.)
2. **2–3 proxy VMs per region** — dedicated, not co-located-per-host. Drivers:
   resiliency, rollover, rolling update. Consequence: the proxy→site hop stays
   over public v6 (the §2.1 caveat persists); we accept that over the
   co-location alternative.
3. **Inbound is the real goal — a VM can attach one public IPv4** (§2.2),
   gated to Atlas-owned VMs today. On DO this is a **reserved IP** + host 1:1
   NAT to the guest. The inbound mirror of the existing NAT44 egress, in the
   same `inet atlas` table. The proxy is its first user; tenant inbound is a
   later step.
4. **No infrastructure-VM tier** — the proxy is an ordinary operator-owned
   `Virtual Machine` (§2.3), invisible to the SPA by ownership. Accepted risk:
   it can be Terminated like any VM (mitigated by running 2–3).
5. **Atlas SSHes into the guest** (§7.3) — a new SSH target type (guest, user
   `atlas`) alongside the existing host-root path, used for both map sync and
   cert push.
6. **Repo placement** — `spec/12-proxy.md` as source of truth, the inbound v4
   primitive added to `spec/06-networking.md`, code under `proxy/` + the two
   `scripts/proxy-*.py`.

### Still open (minor — won't block the scaffold; sensible defaults chosen)

7. **Proxy VM sizing** — sets the per-VM cgroup caps (`vcpus`,
   `memory_megabytes`) and `LimitNOFILE`. Default: a modest VM with generous
   `LimitNOFILE` (e.g. 1048576), tuned once we see real load.
8. **DNS publishing of the 2–3 proxy addresses** — round-robin A/AAAA vs a
   single floating front. Default: publish all 2–3 v4 + v6 as round-robin
   records; Atlas removes a record when a proxy is unhealthy. (A regional load
   balancer / anycast is a later option.)
9. **`Virtual Machine` field for the attached v4** — name + where the DO
   reserved-IP handle is recorded (likely `public_ipv4` + a provider-resource id
   on the VM or Server). Settled while writing `spec/06`.
10. **404-only vs 404/503 tombstones (§6.5)** — shipping 404-only first; the
    known-down 503 path is a small additive follow-up for the signup UX.

---

## 12. Summary

- **The proxy is an Atlas Virtual Machine** — operator-owned, ordinary, running
  the self-built nginx (1.30.2) + OpenResty luajit2 + lua-nginx-module 0.10.29
  stack baked into a `Virtual Machine Image`. No exported rootfs, no host
  service, no bespoke hardening — the **Firecracker jail + per-VM netns + cgroup
  caps are the sandbox**. Install/update/roll = VM provision/rebuild/snapshot.
- **2–3 proxy VMs per region** behind the one regional wildcard (DNS round-robin
  over their v4+v6), giving resiliency and zero-downtime rolling updates.
- **New primitive: a VM can attach one public IPv4** (Atlas-owned only). On DO,
  a reserved IP attached to the host and **1:1-NATed to the guest** — the
  inbound mirror of the existing NAT44 egress, in the same `inet atlas` table.
  The proxy is its first user; the guest contract is unchanged (it still sees
  only its private v4).
- IPv4 + IPv6 on `:443` (HTTP/2, TLS, A+ — DHE dropped, OCSP stapling retired
  per 2026 reality), `:80` redirects. One regional wildcard cert, **pushed into
  each proxy guest by Atlas over SSH**.
- Proxy→site over **public IPv6**; proxy is path-agnostic. Public-path caveat
  (guest firewall must scope `:80`, inbound-:80 probe is a release gate)
  persists because proxies are dedicated, not co-located.
- Subdomain→site map lives in `lua_shared_dict` (truth) in each proxy guest,
  dumped to sorted pretty-printed `map.json` (snapshot, read only at start). Map
  changes are atomic dict writes — **zero reload** at 10k/day churn.
- Admin API on a **unix socket only** inside the guest; **Atlas SSHes into the
  guest** (a new target type) to bulk-declarative `/sync` the full regional map
  to every proxy VM, periodic diff, plus per-entry ops. Cert push uses the same
  guest-SSH path.
- Unmapped subdomain → branded 404/503 from nginx, no upstream.
- `/socket.io` proxied with WebSocket upgrade; `/` with buffering off for Frappe.
- nginx image tested via docker-compose across a config matrix; the **VM
  wrapping** (image sync, boot, v4-attach NAT, guest SSH, rolling rebuild) is a
  new Atlas **e2e use case** proving the host-bound facts.

**All structural decisions are made (§11). Ready to build on approval:**
`spec/12-proxy.md` + the inbound v4-attach addition to `spec/06-networking.md` +
the `proxy/` image scaffold (Dockerfile, nginx.conf, the three Lua modules, the
minimal guest unit, build script) + the two `scripts/proxy-*.py` control-plane
scripts + the docker-compose test harness + the new Atlas e2e use case.
```
