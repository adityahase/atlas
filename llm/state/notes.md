# Hardening — control notes

Working notes for the `hardening` tree. The job: harden the Firecracker **host**
using the Firecracker production-host doc + CIS Ubuntu 24.04 / CIS Distribution-
Independent benchmarks. Scope is host-only (no jailer, no unprivileged user, no
guest changes). The verdict bar: a hardened host re-bootstraps idempotently AND a
VM still boots / gets IPv6 / accepts the key.

This file categorizes every candidate control by importance and **explicitly drops
the rest**. The operator's framing is load-bearing: *focus on issues that might
become blockers to features; ignore things that aren't much value or don't create
blockers; no esoteric measures* (reject #4 + #5). So the categorization axis is not
"what does CIS score" — it is **does this protect a Firecracker host without
breaking it, in a way we can explain in one line and maintain.**

## Why `net.ipv6.conf.all.forwarding = 1` is required (the question asked)

Traced through the code, not assumed:

- There is **no bridge** anywhere (`grep` for bridge/brctl across `scripts/` +
  `atlas/` returns nothing). Each VM is a **per-VM tap** (`atlas-<uuid>`), wired by
  [vm-network-up.sh](../../scripts/vm-network-up.sh). The host has its uplink
  (`eth0`) and N independent tap devices — a pure **routed** topology, not switched.
- Inbound path: DO delivers a VM's `/128` to the **host's** MAC (proxy-NDP answers
  for the VM on the uplink — `vm-network-up.sh:46`), then the host must move the
  packet **from `eth0` to the VM's tap** via the per-VM route
  `ip -6 route VM/128 dev tap` (`vm-network-up.sh:43`). Outbound is the mirror:
  tap → uplink → internet.
- Moving a packet **between two interfaces is, by definition, IP forwarding.** With
  `net.ipv6.conf.all.forwarding = 0` the kernel drops every uplink↔tap packet and
  the VM is dark in both directions. `proxy_ndp = 1` is also a no-op without it —
  proxy-NDP only makes sense on a forwarding (router) host.
- It is therefore **load-bearing and structural**, not incidental. It is set in two
  places: [bootstrap-server.sh:81-87](../../scripts/bootstrap-server.sh) (persisted
  in `/etc/sysctl.d/60-atlas.conf`) and defensively re-applied at each VM start in
  [vm-network-up.sh:34](../../scripts/vm-network-up.sh). Spec basis:
  [06-networking.md:104-134](../../spec/06-networking.md).

**Conclusion → the forwarding conflict is a deliberate, documented deviation, not a
control to apply.** CIS **3.3.1** "Ensure ip forwarding is disabled" is *directly
incompatible* with the product. We keep forwarding on and write down why.

- IPv6 has **no clean "global-off, per-interface-on" split** the way the IPv4 CIS
  remediation leans on (`net.ipv4.ip_forward` global vs. `conf.<if>.forwarding`).
  For IPv6, `conf.all.forwarding=1` is the operative router switch and toggling
  per-interface `forwarding` also changes RA/autoconf behavior. So "scope forwarding
  to the uplink only" is not a real IPv6 knob — don't pretend it is in the plan.
- The honest move: **forwarding stays =1**, and we *contain blast radius at the
  firewall instead* (the `inet atlas` nft `forward` chain already gates which
  traffic crosses — and the Firecracker IMDS drop below tightens it). Document the
  CIS 3.3.1 exception in spec with this rationale.

## The two "reject #1 traps" (controls that would break the product)

These look like normal CIS line items but would break Atlas. They are the reason
"breaks VM boot / SSH / IPv6" is reject #1 and why the e2e bar is reachability, not
an audit score.

| CIS rule | What it says | Why it breaks Atlas | What we do instead |
| --- | --- | --- | --- |
| **3.3.1** Ensure ip forwarding is disabled | `net.ipv6.conf.all.forwarding=0` | Kills all VM networking (above). | **Keep =1.** Documented deviation. |
| **1.1.1.7** Ensure squashfs kernel module is not available | blacklist + don't-load `squashfs` | `unsquashfs` (`squashfs-tools`) unpacks the rootfs image at sync time — bootstrap installs it; the `images` idea depends on it. Blocklisting squashfs breaks image sync. | **Skip this rule.** Documented deviation. |
| **5.1.20** Ensure sshd PermitRootLogin is disabled | `PermitRootLogin no` | Atlas connects as **root, key-only** (no unprivileged user yet — out of scope). `no` locks Atlas out of every server. | **`PermitRootLogin prohibit-password`** — key-only root, the CIS-acceptable middle form. Documented deviation. |

The module blocklist also must **never** touch load-bearing modules: `tun`/`tap`
(taps), `kvm` + `kvm_intel`/`kvm_amd` (Firecracker), `vhost`/`vhost_net` (virtio),
and the `nf_tables`/nft families. CIS only asks to blocklist *unused* fs/protocol
modules — none of these are on that list, but the plan must assert it explicitly.

---

## Category A — APPLY. Real host hardening, zero feature risk, one-line rationale.

These are the tree's actual payload. Each is a sysctl line, an sshd drop-in key, an
apt setting, or a modprobe line — all fold into the existing idempotent bootstrap
pattern (`install -m`, gated writes). Cheap, legible, defensible.

### A1. Firecracker production-host controls (vendor-authoritative — highest priority)

Source: Firecracker `prod-host-setup.md`. These are the ones that specifically
matter for a **microVM host** and the benchmarks don't cover. Ranked by value:

- **IMDS / link-local drop at the host firewall.** Firecracker does *no* traffic
  filtering itself; it recommends an explicit nft drop of guest→`169.254.169.254`
  (cloud metadata) traffic. We have no IPv4 in the guest *today*, but (a) the
  `ipv4-egress` tree is adding NAT44 — at which point the guest can reach v4
  link-local — and (b) the IPv6 equivalent (`fe80::`/link-local + any host-only
  service) deserves the same fence. **This is the single most feature-relevant
  Firecracker control** and slots straight into our `inet atlas` forward chain.
  *Rationale: a guest must not reach host/cloud metadata.* **(Coordinate with
  ipv4-egress — see open questions.)**
- **Disable KSM** (`/sys/kernel/mm/ksm/run = 0`). Page-dedup across VMs is a
  cross-tenant side channel. Likely already off on Ubuntu (verify on the live
  droplet), but assert it. *Rationale: no memory-dedup side channel between VMs.*
- **Disable swap** (or assert none). Guest RAM hitting disk = data remanence; DO
  droplets typically ship swapless, but assert. *Rationale: keep guest memory off
  disk.* (CIS also has mount/`/swap` items — this is the one that matters.)
- **`kvm` module params** the doc calls out, IF cheaply assertable via
  `/etc/modprobe.d/`: `nx_huge_pages` posture and PIT `min_timer_period_us`. These
  are perf/regression knobs more than security — **borderline**, may demote to
  Category C if they add modprobe.d noise without a feature tie. Decide in plan.

Deliberately **NOT** taking from the Firecracker doc (Category D below): `nosmt`,
ECC/TRR memory selection, microcode-at-boot, cgroup favordynmods, `quiet
loglevel=1` — all are BIOS/hardware/boot-cmdline/provider concerns we don't control
on a DO droplet and that don't fold into an idempotent bootstrap script.

### A2. Network sysctls (CIS 3.3, the ones compatible with a routing host)

All go into `/etc/sysctl.d/60-atlas.conf` alongside the existing forwarding lines.
Every one is a one-liner with a clear anti-spoofing / anti-redirect rationale and
**none conflict with forwarding** (a router still wants these):

- **3.3.2** redirect sending disabled (`conf.all.send_redirects=0` v4; we're v6 but
  set both families' analogues where they exist)
- **3.3.5 / 3.3.6** ICMP redirects not accepted (`accept_redirects=0`) — important:
  a hostile guest must not inject routes into the host.
- **3.3.8** source-routed packets not accepted (`accept_source_route=0`)
- **3.3.3** bogus ICMP ignored / **3.3.4** broadcast ICMP ignored
- **3.3.9** log martians (`log_martians=1`)
- **3.3.10** TCP syncookies (`tcp_syncookies=1`)
- **3.3.7** reverse-path filtering (`rp_filter`) — **CAUTION**: rp_filter is IPv4 and
  asymmetric-routing-sensitive; our routed-tap topology is the kind of setup
  strict rp_filter sometimes bites. Verify on the live bench it doesn't drop
  legitimate VM traffic before committing; if it does, drop to loose (`=2`) or
  exclude. Flag in plan.
- **3.3.11** IPv6 router advertisements not accepted (`accept_ra=0`). The guest uses
  **static** addressing, not SLAAC ([06-networking.md:170-179](../../spec/06-networking.md)),
  and the host should not accept RAs on its uplink either. Compatible — assert.

### A3. sshd hardening (CIS 5.1, the lockout-safe subset)

A drop-in at `/etc/ssh/sshd_config.d/60-atlas.conf` (Ubuntu's sshd reads
`sshd_config.d/*` — clean, idempotent, survives package upgrades). Atlas already
connects key-only as root, so these only *tighten* what's already true:

- **5.1.20** `PermitRootLogin prohibit-password` (NOT `no` — see trap table)
- **5.1.19** `PermitEmptyPasswords no`
- **5.1.16** `MaxAuthTries 4`
- **5.1.13** `LoginGraceTime 60`
- **5.1.7** `ClientAliveInterval 300` / `ClientAliveCountMax 3` — **verify** these
  don't kill Atlas's own long-running task SSH sessions (some bootstrap tasks run
  minutes); ClientAlive keeps a session *alive*, so this is usually safe, but a
  task that produces no output for >15min could be reaped. Check task timeouts.
- **5.1.6 / 5.1.15 / 5.1.12** Ciphers / MACs / KexAlgorithms — modern-only sets.
  **CAUTION**: must not exclude an algorithm Atlas's client `ssh` negotiates.
  Atlas uses the system `ssh`; on a modern client these CIS sets are fine, but
  pin only well-supported algos and verify the e2e SSH still connects.
- Password auth: already effectively off (key-only). Assert `PasswordAuthentication
  no` + `KbdInteractiveAuthentication no` — turns "we happen to use keys" into
  "the server refuses anything else." High value, low risk.
- **5.1.5** Banner — **borderline/skip**: a legal banner adds nothing for a
  machine-to-machine SSH control plane and risks confusing the `ssh` client parse.
  Demote to Category C/D.

### A4. Automatic security updates (CIS 1.2.2.1 — the "Ubuntu has a way" the idea hints at)

- Install + enable **`unattended-upgrades`** scoped to the **security** pocket only
  (not all updates — we don't want a feature kernel rolling under a running
  Firecracker host unattended). This is the single highest-value, lowest-risk
  control: it keeps host kernel + openssh + Firecracker's deps patched, which is
  exactly Firecracker's "host kernel must be regularly patched" requirement.
  *Rationale: unattended security patching of the host.*
- **Open question for plan:** auto-reboot. A security kernel update needs a reboot
  to take effect, but an unattended reboot kills every running VM. Default to
  **`Automatic-Reboot "false"`** (patch downloaded/installed, operator reboots on a
  maintenance window) — surfacing "reboot pending" is a future health-check item,
  not this tree. Document the choice.

### A5. Kernel-module blocklist (CIS 1.1.1 fs + 3.2 net, minus squashfs)

One `/etc/modprobe.d/60-atlas-blocklist.conf` with `install <mod> /bin/false` +
`blacklist <mod>` lines for genuinely-unused modules:

- fs: **cramfs (1.1.1.1), freevxfs (1.1.1.2), hfs (1.1.1.3), hfsplus (1.1.1.4),
  jffs2 (1.1.1.5), udf (1.1.1.8)** — none used by Atlas. **usb-storage (1.1.1.9)**:
  fine on a cloud droplet; on bare-metal Self-Managed it could matter, but it's a
  CIS standard and low-risk — include with a note.
  **squashfs (1.1.1.7) is EXCLUDED** (trap table — we need it).
- net protocols: **dccp (3.2.1), tipc (3.2.2), rds (3.2.3), sctp (3.2.4)** — exotic
  protocols, never used, classic remote-attack-surface reducers. Include.
- **Plan must assert** the blocklist contains none of: `tun`, `tap`, `kvm`,
  `kvm_intel`, `kvm_amd`, `vhost`, `vhost_net`, `nf_tables`, `nft_*`.

---

## Category B — APPLY IF CHEAP (fold in only if it's a one-liner; else defer).

- **`/tmp`, `/dev/shm` mount hardening** (CIS 1.1.2.x: `nodev,nosuid,noexec`). Real
  hardening, but on a cloud image `/tmp` is often not a separate mount, and adding
  fstab entries / a tmp.mount unit is fiddly and can fight cloud-init. **Defer** to
  roadmap unless it turns out trivial on the DO image. Low feature relevance.
- **`net.ipv6.conf.all.accept_ra` / `default.accept_ra=0`** — already in A2.3.3.11;
  listed here only to note the host-uplink vs all-interface scoping decision.
- **Process/core-dump limits (CIS 1.5.x: `fs.suid_dumpable=0`, core dumps off)** —
  cheap one-line sysctls, mild value (prevents secrets in core dumps). Include if
  it's literally one line; don't build machinery for it.

## Category C — DEFER to roadmap (valuable but a *different/bigger* tree).

These are real and the spec should name them as deferred, but they break scope
("host hardening only, as root, no guest changes") or are big enough to be their
own idea:

- **Unprivileged `atlas` user + Firecracker jailer.** The biggest one. Already a
  roadmap item ([09-roadmap.md:99-103](../../spec/09-roadmap.md)). Reverses
  "root everywhere" — a breaking, cross-cutting tree of its own. **Out of scope by
  the operator's explicit Scope answer.** Spec must keep listing it as deferred,
  honestly, not claim it's done.
- **Host-key pinning** (TOFU → pinned). Roadmap item
  ([09-roadmap.md:55-65](../../spec/09-roadmap.md)). A `Server` field + a one-time
  capture — additive, but it's a connection-layer change, not host hardening.
- **AppArmor profile for the Firecracker binary.** Firecracker ships an AppArmor
  profile for use *with the jailer*. Without the jailer/unpriv-user it's half a
  measure; pairs naturally with the jailer tree. **Defer with the jailer.**
- **auditd / audit rules (CIS section 6).** Genuinely useful for a multi-tenant
  host, but it's a whole subsystem with its own rule-tuning burden and log volume —
  its own tree, and arguably "above Atlas" (metrics/observability is a non-goal
  this iteration). Defer.
- **Stuck-task reaper / health checks** — already roadmap; the auto-reboot-pending
  surfacing from A4 belongs here.

## Category D — DROP. Esoteric / not-ours / box-ticking (reject #4 + #5).

Explicitly NOT doing these, and the notes say why so it's not re-litigated:

- **`nosmt` / SMT disable.** Halves CPU capacity on a 2-vcpu droplet; a tenancy-
  isolation knob that only matters once we're truly multi-tenant with hostile
  neighbors. Provider/BIOS-level, not a bootstrap concern. **Drop** (note in spec
  as a "if you run hostile multi-tenant, consider" line at most).
- **ECC + TRR memory, microcode-early-load, BIOS settings.** Hardware/provider
  procurement decisions. We run on whatever DO gives us. **Drop.**
- **cgroup favordynmods, `quiet loglevel=1`, KVM PIT tuning.** Boot-cmdline / perf-
  regression knobs, not security, and they need GRUB edits that don't fit an
  idempotent re-runnable bootstrap cleanly. **Drop** (revisit only if a perf
  problem actually shows up — Taste "minimum working, then iterate").
- **Full `usg` CIS profile apply.** Evaluated and rejected as the *mechanism*: usg
  applies hundreds of controls including the three traps above (it would set
  forwarding=0, blocklist squashfs, set PermitRootLogin no) and a long tail of
  password-policy / PAM / AIDE / auditd / banner items that are irrelevant to a
  headless machine-controlled Firecracker host and would be pure breakage +
  maintenance burden. **We hand-apply the cherry-picked subset above instead.** usg
  may still be useful as an **audit** tool (`usg audit`) to *report* a score in the
  e2e — TBD in plan, but never as the apply mechanism. This is the core "no
  box-ticking / no esoteric" decision (reject #4 + #5).
- **PAM / password-quality / account-lockout / pwquality (CIS section 5.3, 5.4).**
  There are no interactive password logins on this host — it's key-only root. Pure
  box-ticking here. **Drop.**
- **AIDE file-integrity, aide cron (CIS 6.x).** Heavy, noisy on a host whose state
  is *meant* to change as VMs come and go. **Drop.**
- **Login banners / MOTD / `/etc/issue` (CIS 1.7, 5.1.5).** Legal-banner theater for
  a machine-to-machine control plane. **Drop.**
- **GDM / X11 / printing / avahi / etc. service disabling (CIS section 2).** A
  server image doesn't run these; chasing them is box-ticking. **Drop** (if the DO
  image happens to ship one we don't want, that's a one-off, not a benchmark sweep).
- **chrony/time-sync hardening (CIS 2.3.x).** The droplet already time-syncs;
  hardening the NTP config is low-value here. **Drop.**

---

## Spec re-categorization (the "recategorize / drop the rest" ask)

How the **spec** should change to reflect this tree, and how the security story gets
recategorized. Spec drift is reject #3, so this is the to-do list for the spec phase.

The current spec scatters security posture across negatives in four files:
- `README.md:28` non-goal: "No jailer, no unprivileged user, no SELinux or AppArmor.
  **Root everywhere.**"
- `06-networking.md:221-228` "What we do not do" (per-VM firewall, etc.)
- `09-roadmap.md` deferred: unprivileged-user+jailer (l.99-103), host-key pinning
  (l.55-65), secret indirection (l.73-77).
- `03-bootstrapping.md` lists bootstrap steps but says nothing about hardening.

**Proposed recategorization — give hardening a *home*, stop scattering it:**

1. **New section in `03-bootstrapping.md`: "Host hardening."** Bootstrap is where it
   happens, so it's documented where it runs. Lists the applied controls (A1–A5) as
   a short table with one-line rationales, and — crucially — the **three documented
   deviations** (forwarding stays on / squashfs kept / root-key-login kept) with
   *why*, so a future reader running a CIS audit and seeing those three "failures"
   understands they're deliberate, not drift. This is the single most important spec
   edit: **the deviations must be written down or they read as bugs forever.**
2. **Rewrite `README.md:28` non-goal.** "No SELinux or AppArmor. Root everywhere"
   becomes honest: the host *is* hardened (sysctls/sshd/updates/modules) but still
   runs Atlas operations **as root** — the privilege-drop (unpriv user + jailer +
   AppArmor) remains explicitly deferred. Don't let the spec claim more or less than
   what's true.
3. **`06-networking.md`:** the forwarding deviation gets a back-reference to the new
   hardening section (so the `forwarding=1` line and its CIS-3.3.1 exception live
   next to each other). If the IMDS/link-local drop ships, document the new nft rule
   in the per-VM rules list (l.137-151).
4. **`09-roadmap.md`:** keep jailer/unpriv-user, host-key pinning, AppArmor as
   deferred (Category C), and *add* the items this tree consciously deferred (tmp
   mount hardening, auditd, auto-reboot-pending surfacing) so they're captured, not
   lost. Per WORKFLOW step 6, only add roadmap items not already there.
5. **`README.md` operator-use-case table / testing:** hardening doesn't add an
   operator button, so per the spec's own rule ("Add a new use-case module only when
   the operator gets a new button") the e2e coverage **extends `server_provisioning`**
   (idempotent re-bootstrap of a hardened host) and leans on the existing
   `virtual_machine_provisioning` reachability check — no new use-case row.

**Drop from consideration (so the spec stays lean):** everything in Category D does
NOT get a spec mention beyond, at most, a single roadmap line noting multi-tenant
SMT/ECC concerns are a provider-procurement matter "above Atlas." We do not want the
spec to grow a compliance-checklist appendix — that would itself be box-ticking.

---

## Open questions to resolve in the plan (no open questions allowed in the plan itself)

1. **IMDS/link-local nft rule + ipv4-egress coordination.** ipv4-egress is adding
   NAT44 and a masquerade rule on the same uplink / same `inet atlas` table. The
   IMDS-drop rule must land in a way that composes with (doesn't get masqueraded
   around by) that change. *images is merging now; ipv4-egress is mid-flight.* Decide
   whether the IMDS drop ships in THIS tree or is explicitly handed to ipv4-egress as
   a requirement. **Lean: ship the IPv6 link-local fence here; note the v4 IMDS drop
   as ipv4-egress's job since it owns the v4 forward path.** Confirm against live bench.
2. **usg: present on the DO 24.04 image? audit-only usable?** Verify `usg` exists /
   installs, and whether `usg audit` can produce a parseable pass/fail count for the
   e2e to assert on — without `usg fix` ever running. If usg is awkward, the e2e
   asserts specific readbacks (sshd_config -T, sysctl readback, modprobe -n) instead.
3. **rp_filter on the routed-tap topology.** Verify strict `rp_filter=1` doesn't drop
   legit asymmetric VM traffic before committing 3.3.7. Fallback: loose or exclude.
4. **ClientAlive vs. long bootstrap tasks.** Confirm 300s/3 doesn't reap Atlas's own
   task sessions. Cross-check against Task timeouts in `04-tasks.md`.
5. **Cipher/MAC/Kex sets vs. the system `ssh` client Atlas uses.** Pin sets that the
   client definitely negotiates; verify e2e SSH still connects.
6. **24.04 vs 26.04 portability.** sshd_config.d, sysctl.d, modprobe.d, and
   unattended-upgrades are all stable across both — but the CIS *rule numbers* and
   any usg profile name are 24.04-specific. Express controls by mechanism (drop-in
   files), not by invoking a 24.04-pinned tool, so 26.04 doesn't break (per intake).
7. **KSM/swap: assert-off vs. force-off.** Decide whether to fail the bootstrap if
   swap/KSM is unexpectedly on (fail-loud, Taste 17) or just set them off idempotently.
   Lean: set off idempotently; they're host-state we own.
