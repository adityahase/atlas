#!/bin/bash
# Hardening e2e: read back the host-hardening state bootstrap-server.sh applies.
# Asserts both that the CIS controls are present AND that the three deliberate
# deviations hold (forwarding on, squashfs NOT blocklisted, root key-login kept).
# Fail-loud: any missing/wrong control exits non-zero and fails the Task.
set -euo pipefail

fail() { echo "HARDENING FAIL: $1" >&2; exit 1; }

# --- sshd: key-only root, password auth off (CIS 5.1; deviation: NOT `no`) ---
sshd_config="$(sudo sshd -T)"
echo "$sshd_config" | grep -qx "permitrootlogin prohibit-password" \
    || fail "PermitRootLogin is not prohibit-password"
echo "$sshd_config" | grep -qx "passwordauthentication no" \
    || fail "PasswordAuthentication is not no"
echo "$sshd_config" | grep -qx "permitemptypasswords no" \
    || fail "PermitEmptyPasswords is not no"
echo "$sshd_config" | grep -qx "maxauthtries 4" \
    || fail "MaxAuthTries is not 4"
echo "sshd OK (prohibit-password, no password auth, MaxAuthTries 4)"

# --- forwarding deviation: MUST stay on or every VM goes dark (CIS 3.3.1) ---
[ "$(cat /proc/sys/net/ipv6/conf/all/forwarding)" = "1" ] \
    || fail "ipv6 forwarding is off — VM networking would be dead"
echo "forwarding deviation OK (net.ipv6.conf.all.forwarding=1)"

# --- a sample CIS 3.3 sysctl is actually applied ---
[ "$(cat /proc/sys/net/ipv4/conf/all/accept_redirects)" = "0" ] \
    || fail "net.ipv4.conf.all.accept_redirects is not 0"
[ "$(cat /proc/sys/net/ipv4/tcp_syncookies)" = "1" ] \
    || fail "net.ipv4.tcp_syncookies is not 1"
echo "network sysctls OK (accept_redirects=0, tcp_syncookies=1)"

# --- module blocklist: an unused module is blocked; squashfs is NOT ---
# Capture into a var first: under `set -o pipefail`, modprobe -n's exit code
# would otherwise leak into the pipeline and produce a false result.
dccp_probe="$(sudo modprobe -n -v dccp 2>&1 || true)"
case "$dccp_probe" in *"/bin/false"*) ;; *) fail "dccp is not blocklisted" ;; esac
# squashfs deviation: unsquashfs needs it, so it must remain loadable.
squashfs_probe="$(sudo modprobe -n -v squashfs 2>&1 || true)"
case "$squashfs_probe" in
    *"/bin/false"*) fail "squashfs is blocklisted — image sync would break" ;;
esac
echo "module blocklist OK (dccp blocked, squashfs kept)"

# --- unattended security updates enabled (CIS 1.2.2.1) ---
test -f /etc/apt/apt.conf.d/60-atlas-unattended.conf \
    || fail "unattended-upgrades config missing"
dpkg -s unattended-upgrades >/dev/null 2>&1 \
    || fail "unattended-upgrades package not installed"
echo "unattended-upgrades OK"

# --- KSM off (no cross-VM memory side channel) when KSM is present ---
if [ -r /sys/kernel/mm/ksm/run ]; then
    [ "$(cat /sys/kernel/mm/ksm/run)" = "0" ] || fail "KSM is running"
    echo "KSM OK (off)"
else
    echo "KSM OK (not present)"
fi

echo "HARDENING PROBE OK"
