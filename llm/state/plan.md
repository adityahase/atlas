# Plan — hardening

Host-level hardening of the Firecracker server, applied in the bootstrap path.
Companion analysis (control-by-control categorization, dropped items, the
forwarding investigation) lives in [notes.md](./notes.md). This file is the
executable phasing: small, testable, verifiable, no open questions.

**Bar (from intake):** a hardened host re-bootstraps **idempotently** (re-run =
clean no-op, exit 0) AND a VM still **boots / gets IPv6 / accepts the key**. Reject
on: breaking that bar · logic in Python not the script · spec not updated ·
box-ticking · esoteric controls.

## Shape of the change

All hardening folds into the **existing** [bootstrap-server.sh](../../scripts/bootstrap-server.sh)
pattern (`install -m`, gated writes, `||` guards) — it is already an idempotent
single-SSH-session script and hardening is a host-setup concern, so it belongs
there, not in a new Python method (Taste 11–13; reject #2). We add **four drop-in
files** the script writes and **a few in-place assertions**:

- `/etc/sysctl.d/60-atlas.conf` — **extend** the existing file (forwarding/proxy-ndp
  already there) with the CIS-3.3 network sysctls.
- `/etc/ssh/sshd_config.d/60-atlas.conf` — **new**, sshd hardening drop-in.
- `/etc/modprobe.d/60-atlas-blocklist.conf` — **new**, module blocklist.
- `/etc/apt/apt.conf.d/60-atlas-unattended.conf` (+ install `unattended-upgrades`).
- in-place: KSM off, swap-off assertion, `/dev/kvm` perm check (already present).

Naming uses the established `60-atlas` prefix so all Atlas-owned config sorts and
greps together. Drop-in files (not edits to stock configs) keep us idempotent and
**26.04-portable** — we never invoke a 24.04-pinned tool (resolves portability;
intake). usg is **not** the apply mechanism (notes Category D); it may be an
audit-only check in tests (Phase 6, decided below).

## The three documented deviations (load-bearing — see notes trap table)

These are **not bugs**; the spec must record them (Phase 7) or they read as drift
forever:
1. **CIS 3.3.1 forwarding stays `=1`.** Required by the routed-tap networking model;
   `=0` makes every VM dark. (Full trace in notes.) Blast radius contained at the
   `inet atlas` nft forward chain, not at the global switch.
2. **CIS 1.1.1.7 squashfs kept.** `unsquashfs` unpacks the rootfs image; blocklisting
   it breaks image sync.
3. **CIS 5.1.20 `PermitRootLogin prohibit-password`** (not `no`). Atlas is key-only
   root; no unprivileged user yet (out of scope).

---

## Phases

Each phase is one cohesive edit + its static check. The **single-bench rule** means
no phase runs against a live host until the operator flips the tree — so phases 1–6
are coded with `bash -n` / `py_compile` / readback-logic review as the per-phase
gate, and Phase 8 is the one turn-taking verification checkpoint.

### Phase 1 — Network sysctls (CIS 3.3 compatible subset)
Extend the `60-atlas.conf` heredoc in bootstrap-server.sh (after the existing
forwarding/proxy_ndp lines) with: `accept_redirects=0`, `secure_redirects=0`,
`send_redirects=0`, `accept_source_route=0`, `log_martians=1`, `tcp_syncookies=1`,
`icmp_ignore_bogus_error_responses=1`, `icmp_echo_ignore_broadcasts=1`, IPv6
`accept_ra=0`. **rp_filter is NOT included** pending the live-bench check (notes
Q3 / Phase 8); add it only if Phase 8 proves it doesn't drop VM traffic.
Comment each line with its one-line rationale. Keep `forwarding=1` exactly as is
with a comment pointing at the deviation.
*Gate:* `sysctl -p`-shape review; values don't contradict forwarding.

### Phase 2 — sshd hardening drop-in
Write `/etc/ssh/sshd_config.d/60-atlas.conf` via `install -m 0644 /dev/stdin`:
`PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
`KbdInteractiveAuthentication no`, `PermitEmptyPasswords no`, `MaxAuthTries 4`,
`LoginGraceTime 60`, `ClientAliveInterval 300`, `ClientAliveCountMax 3`, modern
`Ciphers`/`MACs`/`KexAlgorithms` sets. **No `Banner`** (notes D). Reload sshd
idempotently (`sshd -t` validate THEN `systemctl reload ssh`) — validate-before-
reload so a bad drop-in never bricks SSH (fail-loud, Taste 17).
*ClientAlive safety (resolves notes Q4):* Atlas's client sets `ConnectTimeout=30`
but no `ServerAliveInterval` ([transport.py:22-27](../../atlas/atlas/_ssh/transport.py));
tasks run one synchronous ssh exec up to 1800s ([runner.py:30](../../atlas/atlas/_ssh/runner.py)).
`ClientAliveInterval` probes are answered by the **client's ssh transport at the
protocol layer**, independent of script output — so a silent long task (e.g.
`apt-get`) stays connected. 300×3=900s server-side reaping only triggers on a truly
dead client, which is the desired behavior. Safe to adopt.
*Cipher safety (resolves notes Q5):* pin only algos a modern OpenSSH client
negotiates by default; verified in Phase 8 by the e2e SSH connecting at all.
*Gate:* `bash -n`; drop-in keys spelled exactly as `sshd -T` reports them.

### Phase 3 — Kernel-module blocklist (CIS 1.1.1 fs + 3.2 net, minus squashfs)
Write `/etc/modprobe.d/60-atlas-blocklist.conf`: `install <m> /bin/false` +
`blacklist <m>` for `cramfs freevxfs hfs hfsplus jffs2 udf usb-storage` and
`dccp tipc rds sctp`. **squashfs EXCLUDED** (deviation #2). Add a guard comment
listing the modules we must NEVER blocklist (`tun tap kvm kvm_intel kvm_amd vhost
vhost_net nf_tables nft_*`) and assert none appear. No `modprobe -r` of already-
loaded modules (don't yank a module out from under a running system); blocklist
prevents future loads, which is the CIS intent and the idempotent choice.
*Gate:* grep the file for the forbidden set → must be empty.

### Phase 4 — Automatic security updates (CIS 1.2.2.1)
`apt-get install -y unattended-upgrades` (folds into the existing apt block, after
the package install). Write `/etc/apt/apt.conf.d/60-atlas-unattended.conf`
enabling **security pocket only**, `Automatic-Reboot "false"` (an unattended reboot
would kill running VMs — notes A4 / reboot-pending surfacing is a roadmap item).
*Gate:* `bash -n`; config scoped to security origin only.

### Phase 5 — Firecracker host controls: KSM off + swap assertion
In-place, idempotent: `echo 0 > /sys/kernel/mm/ksm/run` (guarded — file may be
absent if KSM not compiled in: `[ -w /sys/kernel/mm/ksm/run ] && echo 0 > ...`).
Swap: assert-and-disable idempotently (`swapoff -a` is idempotent; a DO droplet is
typically swapless). Both get a one-line rationale (no cross-VM memory side
channel / no guest RAM on disk). KVM module-param tuning (nx_huge_pages, PIT)
**deferred to Category C** — perf knobs, not security, not worth modprobe.d noise
(decided; not an open question).
*Gate:* `bash -n`; both steps no-op cleanly when KSM/swap already off.

### Phase 6 — e2e coverage (extend, don't add a use case)
Hardening adds **no operator button**, so per the spec's own rule it does **not** get
a new use-case module — it extends the existing ones
([README.md:182-186](../../spec/README.md)):
- **`server_provisioning`**: add an **idempotent re-bootstrap** assertion — run
  `bootstrap()` a second time on the shared host, assert exit 0 and that the four
  drop-in files are byte-identical (no drift), and read back the hardened state on
  the host: `sshd -T | grep permitrootlogin` → `prohibit-password`; `sysctl
  net.ipv6.conf.all.forwarding` → `1` (deviation holds); a sample CIS sysctl (e.g.
  `net.ipv4.conf.all.accept_redirects`) → `0`; `modprobe -n -v dccp` → blocked;
  `modprobe -n -v squashfs` → NOT blocked (deviation #2 holds); `unattended-upgrades`
  unit enabled.
- **`virtual_machine_provisioning`**: unchanged, but it is the load-bearing proof —
  a VM provisioned **after** hardening must still boot, get IPv6, accept the key.
  Reuse the existing reachability check; no new code beyond running it post-harden.
*usg decision (resolves notes Q2):* **do not** depend on usg. The readbacks above
are exact, parseable, and tool-independent (work on 24.04 and 26.04). If `usg` turns
out present on the bench we may add an *audit-only* score log line, but the test
assertions stand on the readbacks, never on usg. (Avoids a 24.04-pinned dependency.)
*Gate:* tests written; `py_compile`; run deferred to Phase 8.

### Phase 7 — Spec update (reject #3)
Per the re-categorization plan in [notes.md](./notes.md#spec-re-categorization):
1. **New "Host hardening" section in `03-bootstrapping.md`** — applied controls
   table (A1–A5) with one-line rationales + the **three deviations** and why. This
   is the single most important edit: the deviations must be written down.
2. **Rewrite `README.md:28` non-goal** — host *is* hardened (sysctls/sshd/updates/
   modules) but Atlas still operates **as root**; jailer + unprivileged user +
   AppArmor remain explicitly **deferred**, not done.
3. **`06-networking.md`** — back-reference the forwarding deviation next to the
   `forwarding=1` line; document any new nft rule if the link-local fence ships.
4. **`09-roadmap.md`** — keep jailer/unpriv-user, host-key pinning, AppArmor as
   deferred; add tmp-mount hardening, auditd, auto-reboot-pending (only items not
   already there — WORKFLOW step 6).
No new operator-use-case row (no new button).

### Phase 8 — VERIFY (turn-taking checkpoint — operator flips `atlas-tree hardening`)
Batch all live checks here (single-bench rule). Run order:
1. Bootstrap a fresh droplet → hardened, `Active`.
2. **Idempotency:** re-run Bootstrap → exit 0, drop-ins byte-identical, no drift.
3. **Reachability (reject #1):** provision a VM on the hardened host → boots, gets
   IPv6, `ping6` + SSH-key login succeed. **This is the gate.**
4. **rp_filter probe (notes Q3):** with VM up, check whether enabling strict
   `rp_filter` would drop VM traffic; only then decide to add Phase 1's rp_filter
   line. If it bites → leave it out / use loose, document.
5. **Atlas-to-host SSH still works** through the new sshd drop-in (the whole suite
   running at all proves cipher/Kex/ClientAlive didn't lock us out).
6. Readback assertions from Phase 6 pass.
Then loop back to fix anything Phase 8 surfaces before READY.

---

## What we are NOT doing (scope fence — intake + notes Categories C/D)

- **No jailer, no unprivileged `atlas` user, no dropping root SSH.** Stays a roadmap
  item; this tree hardens the host *as root*. (Operator's explicit Scope answer.)
- **No AppArmor/SELinux profile.** Pairs with the jailer; deferred with it.
- **No guest/image changes**, no guest firewall, no networking-model change.
- **No full `usg`/CIS profile apply.** We hand-apply the cherry-picked subset; usg is
  at most an audit reporter. (Reject #4/#5.)
- **No PAM/password-policy/pwquality/account-lockout, no AIDE, no auditd, no login
  banners/MOTD, no service-disable sweep, no time-sync hardening.** Box-ticking for a
  headless key-only-root machine-controlled host. (Notes Category D.)
- **No `nosmt`, ECC/TRR, microcode-early-load, cgroup favordynmods, GRUB cmdline
  tuning, KVM PIT/nx_huge_pages params.** Hardware/provider/boot-cmdline concerns that
  don't fit an idempotent re-runnable bootstrap. (Notes Category D.)
- **No `/tmp` `/dev/shm` mount hardening** unless it proves trivial on the DO image
  (Category B); else roadmap.
- **No auto-reboot** on unattended security updates (would kill running VMs).
- **No new operator button / no new e2e use-case module.**

## Resolved (no open questions remain in this plan)

- Forwarding deviation — resolved: keep `=1`, document, contain at nft. (notes)
- squashfs / PermitRootLogin deviations — resolved as above.
- ClientAlive vs long tasks — resolved safe (Phase 2 reasoning).
- usg apply-vs-audit — resolved: never apply; audit-only at most. (Phase 6)
- 26.04 portability — resolved: drop-in files + readbacks, no 24.04-pinned tool.
- rp_filter — resolved as a **Phase-8-gated** add (the one thing that genuinely needs
  the live host to decide); default is to omit it until proven safe, so the plan has
  a definite default, not an open question.
- KVM param tuning / KSM-force-vs-assert / IMDS-v4-drop ownership — resolved: KVM
  params dropped (Cat C); KSM set-off idempotently; the **v4 IMDS drop is
  ipv4-egress's job** (it owns the v4 forward path), this tree ships the host
  sysctl/sshd/module/update hardening only. (notes Q1/Q7)
```
