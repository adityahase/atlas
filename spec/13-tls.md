# TLS & Domain Layer

The reverse proxy ([12-proxy.md](./12-proxy.md)) terminates TLS for
`*.<region>.frappe.dev` with a wildcard cert it receives through
`atlas.atlas.proxy.push_cert(vm, fullchain, privkey)`. That function was always a
**consumer with no producer** — nothing in Atlas issued the PEMs it expects. This
layer is the producer: it tracks root domains, issues their regional wildcard cert
via Let's Encrypt over a DNS-01 challenge, and pushes the result onto every proxy
VM in the domain's region.

The shape mirrors the compute Provider abstraction
([01-architecture.md](./01-architecture.md)): two small registries (DNS, TLS),
each an ABC with one implementation per vendor type, resolved by name so callers
never branch on `provider_type`.

## The flow

```
Root Domain ──Issue / Renew Certificate──▶ TLS Certificate.issue()
                          │  TlsProvider.issue(domain, dns_provider)
                          │  → issue-cert.py Task on the CONTROLLER (certbot DNS-01)
                          ▼
                       PEMs on the controller's disk (fullchain_path, privkey_path)
                          │
                          ▼  _push_to_proxies(): for vm in proxies(region)
                       atlas.atlas.proxy.push_cert(vm, fullchain, privkey)   ← EXISTING
                          │     ▼
                          │  nginx reload on each proxy guest
                          ▼  then publish the public routing record:
                       dns_provider.upsert_wildcard(domain, fleet A+AAAA)
                          ▼
                       *.<domain> A → proxy reserved IPv4s, AAAA → proxy /128s
```

One `Root Domain` row == one region == one wildcard. `Root Domain.region` is the
join key to the proxy fleet (`Virtual Machine.is_proxy=1, region=<region>` — the
same query `proxy._proxy_vms_in_region` already uses), so issuance never needs to
know which VMs are proxies; it asks the region.

## Abstractions

Two registries under `atlas/atlas/`, each modeled on `atlas/atlas/providers/`:

- **`dns/`** — the DNS seam. `DnsProvider(ABC)`: `authenticate()`,
  `credential_env()` (vendor secrets as the env certbot's plugin reads),
  `certbot_authenticator()` (the plugin NAME, e.g. `route53`), and
  `upsert_wildcard(domain, targets)` (publish the public `*.<domain>` A/AAAA
  records that point the regional wildcard at the proxy fleet — A → the proxies'
  reserved IPv4s, AAAA → their `/128`s, round-robin). `for_domain_provider(name)`
  resolves a `Domain Provider` row to an instance. `Route53DnsProvider` is the
  only implementation; Cloudflare is a reserved Select option.

  The challenge TXT records are certbot's job (Atlas never writes them); the
  durable `*.<domain>` record is Atlas's, reconciled by `TLS Certificate`'s
  `_push_to_proxies` on every issue/renew/push (so a rebuilt proxy's new `/128`
  or a reattached reserved IP is reflected). Without it the cert proves identity
  but `<sub>.<domain>` resolves to nothing.
- **`tls/`** — the issuer seam. `TlsProvider(ABC)`: `authenticate()` and
  `issue(domain, dns_provider) -> IssuedCert` (on-disk PEM paths + validity
  window). `for_tls_provider(name)` resolves a `TLS Provider` row.
  `LetsEncryptProvider` is implemented; `ZeroSslProvider` is a stub
  (`frappe.throw`); `SelfManagedTlsProvider` expects operator-supplied PEMs.

Atlas talks to DNS/TLS vendors only through these interfaces.

## The issue-cert Task runs on the controller

Certificate issuance is the first **controller-local** Task: the ACME client runs
where the PEMs land (the controller, which the proxy control plane reaches from),
and there is no remote host to stage a script onto. So:

- `scripts/issue-cert.py` is an ordinary typed-CLI Task
  ([04-tasks.md](./04-tasks.md)) — `IssueCertInputs.from_args()` in,
  `IssueCertResult.emit()` (the one `ATLAS_RESULT=` line) out — but it is invoked
  by `atlas.atlas.local_task.run_local_task` as a **local subprocess**, not over
  SSH. It is excluded from `scripts_catalog.allowed_scripts()` (the host run-task
  gate) via `CONTROLLER_ONLY`, so it never appears as a host Task or in the
  operator picker, but `resolve()` still finds it for the local runner.
- A `Task` row is still recorded, so a cert issuance shows up in the same audit
  list as every host/guest op.
- The DNS authenticator name crosses the CLI as a plain value (`route53`), never a
  `--`-prefixed token — the script renders `--dns-route53` itself. (Passing a
  `--`-value through argparse's repeated-flag form silently breaks; the name form
  sidesteps it.)
- Vendor credentials (AWS keys) travel through the subprocess **environment**,
  never argv, so they never appear in `ps`.

certbot + `certbot-dns-route53` + openssl are a **controller-host dependency**
(documented here; install on the Atlas controller). They are *not* a server- or
script-runtime dependency, so the server-side "stdlib only" rule
([04-tasks.md](./04-tasks.md), principle #5) is intact: `scripts/lib/atlas/certs.py`
is pure stdlib string logic, and the two subprocess calls (certbot, openssl) live
in the entry point.

On-disk layout, controller-local: `~/.atlas/certbot/<domain>/` (certbot
config/work/logs), with the live PEMs at
`~/.atlas/certbot/<domain>/live/<domain>/{fullchain,privkey}.pem`. Sibling of the
SSH `~/.atlas/known_hosts`, so all controller-local Atlas state sits together. The
`TLS Certificate` row stores only the **paths** — private-key bytes stay out of
the DB, mirroring `Atlas Settings.ssh_private_key_path`.

## Renewal

- **Manual:** **Issue / Renew Certificate** on `Root Domain` (creates/locates the
  cert and issues), **Issue/Renew** + **Push to Proxies** on `TLS Certificate`.
- **Scheduled:** a `daily` `scheduler_events` hook →
  `atlas.atlas.doctype.tls_certificate.tls_certificate.renew_expiring`: every
  `Active` cert whose `expires_on` is within 30 days is re-issued **and**
  re-pushed, then its status returns to `Active`. Mirrors the proxy reconcile
  philosophy — the desired state (a fresh cert on every proxy) is continuously
  restored. certbot is idempotent (`--keep-until-expiring` renews-or-skips), so a
  renewal that isn't due yet is a cheap no-op.

A push to one wedged proxy never blocks the others: `_push_to_proxies` logs the
failure and moves on, exactly like `proxy.reconcile_region`.

## First-run order

Layered on top of the proxy first-run ([12-proxy.md](./12-proxy.md)):

1. **Domain Provider** — one row, `provider_type = Route53`.
2. **Route53 Settings** — IAM access key + secret with `route53:*` on the zone.
3. **TLS Provider** — one row, `provider_type = Let's Encrypt`.
4. **Lets Encrypt Settings** — ACME directory (staging while testing), account
   email, agree-to-ToS.
5. **Root Domain** — one row per region: `domain = <region>.frappe.dev`,
   `region`, link the Domain + TLS providers. Click **Issue / Renew Certificate**.

After issuance the regional wildcard is on every proxy VM in the region and nginx
has reloaded; the proxy now serves `https://*.<region>.frappe.dev` with a real
cert.

> The DocType name is **"Lets Encrypt Settings"** (no apostrophe): Frappe scrubs a
> DocType name into a Python module path, and `Let's Encrypt Settings` scrubs to
> `let's_encrypt_settings` — an apostrophe in a module path is unimportable. The
> issuer's `provider_type` Select value keeps the apostrophe (`Let's Encrypt`)
> since that is data, not a module.
