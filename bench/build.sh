#!/usr/bin/env bash
# Bake the golden bench image — run INSIDE a freshly-provisioned Ubuntu guest
# (plans/self-serve/01-golden-image.md). Installs bench-cli, then runs
# `bench init` so the heavy, per-site-invariant work — apt MariaDB + Redis, the
# uv venv, the Frappe clone, Node + npm deps, the admin frontend — is baked once
# into the image instead of paid per site. The built VM is then snapshotted by
# Atlas; that snapshot is the reusable "golden bench image" `deploy-site.py`
# lands on, leaving it only `bench new-site` + start.
#
# This mirrors proxy/build.sh: the AUTHORITATIVE build, uploaded verbatim and run
# over guest-SSH by atlas.atlas.bench_image.build_bench. Idempotent (spec taste
# #14: retry = re-run) — bench-cli's `init` is itself idempotent, and re-cloning
# bench-cli is a `git pull`.
#
# Bakes a SITE too, under the fixed standard name `site.local`, then leaves the
# bench STOPPED. The slow `bench new-site` (DB schema + frappe install + migrate)
# is the longest per-signup step; baking it once and RENAMING the baked site to
# the per-VM FQDN at deploy time (deploy-site.py, a directory move) moves that
# cost off the signup path entirely. The routing identity (Contract A) is still
# per-VM — it is the rename target, applied per clone, not baked. MariaDB + Redis
# are installed + enabled-on-boot so the baked site (and the renamed clone) find
# them up after the snapshot boots.
#
# Run as root. Reads the committed tree from the directory this script lives in.

set -euo pipefail

# --- Pinned versions. Bumping any of these is a deliberate image update rolled
# as a new golden snapshot (the same discipline proxy/build.sh's pins follow).
# bench-cli is pinned to a commit, not `main`, so the bake is reproducible; the
# Frappe branch is pinned in bench.toml (frappe version-16). ---
BENCH_CLI_REPO="https://github.com/frappe/bench-cli"
BENCH_CLI_REF="5a506211f7631ee320415480f4098efa81ae780b"  # main @ 2026-06-03

BENCH_CLI_DIR="/root/bench-cli"
BENCH_NAME="atlas"
# The baked site. A clone of this image already carries a fully-created Frappe
# site under this name; deploy-site.py renames it to the per-VM FQDN at deploy
# time (a directory move, not a `bench new-site`) — see that script and the
# README "Serving model". Kept in lockstep with bench/deploy-site.py's BAKED_SITE.
BAKED_SITE="site.local"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export DEBIAN_FRONTEND=noninteractive

# --- 1. Base packages bench-cli shells out to. bench-cli is stdlib-only but its
# `init` invokes apt (MariaDB/Redis), git, curl, and a C toolchain for the
# mariadb python driver. uv is auto-installed by install.sh; we install git/curl
# up front so the clone + uv fetch work on a minimal rootfs. nginx + supervisor
# are baked here (not at deploy time) because deploy-site.py runs `bench setup
# production`, which generates + reloads their configs to serve sites on :80 —
# installing them per-site would add apt latency to every signup. ---
apt-get update
apt-get install -y --no-install-recommends \
	ca-certificates curl git build-essential pkg-config \
	nginx supervisor

# --- 2. Install bench-cli at the pinned commit (the install.sh recipe, but
# pinned — never `curl | bash` of a moving main at boot). Clone-or-update so a
# re-run is a fast-forward, then check out the exact ref. uv is installed to
# /root/.local/bin; put both it and bench on PATH for this build. ---
if [ -d "$BENCH_CLI_DIR/.git" ]; then
	git -C "$BENCH_CLI_DIR" fetch --quiet origin
else
	git clone --quiet "$BENCH_CLI_REPO" "$BENCH_CLI_DIR"
fi
git -C "$BENCH_CLI_DIR" checkout --quiet "$BENCH_CLI_REF"
chmod +x "$BENCH_CLI_DIR/bench"

if ! command -v uv >/dev/null 2>&1; then
	curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$BENCH_CLI_DIR:/root/.local/bin:$PATH"

# Persist PATH for every future login shell (deploy-site.py reaches bench over a
# fresh SSH session, which sources /etc/profile.d). Idempotent: overwrite.
install -m 0644 /dev/stdin /etc/profile.d/atlas-bench.sh <<EOF
export PATH="$BENCH_CLI_DIR:/root/.local/bin:\$PATH"
EOF

# --- 3. Create the bench from the committed bench.toml (pins Frappe + the
# localhost-only MariaDB root password — see bench.toml header) and init it.
# `bench new` scaffolds benches/<name>/; we drop our pinned bench.toml over the
# generated one so the image's Frappe branch + db secret are the committed ones,
# not bench-cli's template defaults. ---
BENCH_DIR="$BENCH_CLI_DIR/benches/$BENCH_NAME"
if [ ! -f "$BENCH_DIR/bench.toml" ]; then
	"$BENCH_CLI_DIR/bench" new "$BENCH_NAME"
fi
install -m 0644 "$SRC_DIR/bench.toml" "$BENCH_DIR/bench.toml"

# `bench init` is the heavy, idempotent step: installs + starts + enables MariaDB
# and Redis, builds the uv venv, clones + installs Frappe, installs Node + npm
# deps, downloads the admin frontend. Everything here is per-site-invariant, so
# baking it is the whole point of the golden image. The baked site below adds the
# (also slow, also invariant-except-for-the-name) `bench new-site` to that.
"$BENCH_CLI_DIR/bench" -b "$BENCH_NAME" init

# --- 4. Enable MariaDB + Redis on boot. `bench init` started MariaDB for the
# build; make sure it (and Redis, which bench-cli runs from its Procfile, so
# enable the apt one too if present) comes back after the snapshot boots — a
# per-site `bench new-site` dials MariaDB on localhost. No live systemd in some
# build envs, so guard on /run/systemd/system (matches proxy/build.sh §8). ---
if [ -d /run/systemd/system ]; then
	systemctl enable mariadb.service 2>/dev/null || systemctl enable mysql.service 2>/dev/null || true
	systemctl enable redis-server.service 2>/dev/null || true
	# nginx + supervisor front the site on :80 after `bench setup production`
	# (deploy-site.py); enable-on-boot so a snapshot-booted clone serves without a
	# re-deploy. supervisor runs the gunicorn/socketio/worker processes the bench's
	# nginx proxies to.
	systemctl enable nginx.service 2>/dev/null || true
	systemctl enable supervisor.service 2>/dev/null || true
fi

# Remove the stock Ubuntu default nginx vhost. It listens `[::]:80 default_server`
# (server_name _), so it OWNS the IPv6 :80 socket and answers 404 to every v6
# request that doesn't match a named vhost — and the edge proxy reaches each site
# over its public /128 (IPv6 is the only inbound path, vm-inbound-ipv6-only). Left
# in place it silently shadows the real site on the v6 path while v4 looks fine.
# deploy-site.py separately adds `listen [::]:80;` to the per-site vhost; both are
# needed. Idempotent (`-f`), and bake-time because the vhost ships with the nginx
# apt package installed above.
rm -f /etc/nginx/sites-enabled/default

# --- 5. Bake the site. The heavy per-site work — `bench new-site` (create the
# MariaDB schema, install + migrate frappe) — is the multi-minute step we move
# OUT of every signup by paying it ONCE here, under a fixed standard name. A
# clone of this image renames this baked site to its own FQDN at deploy time (a
# directory move, deploy-site.py), so the slow create is never on the signup
# path. The baked Administrator password is a throwaway: deploy-site.py resets it
# to a freshly-generated per-VM secret at rename time, so the baked one never
# reaches a user (the per-site-secret discipline of D01-3 still holds — only the
# db root password is shared, localhost-only).
#
# Idempotent: skip if the baked site already exists (a re-bake / re-run).
if [ ! -d "$BENCH_DIR/sites/$BAKED_SITE" ]; then
	"$BENCH_CLI_DIR/bench" -b "$BENCH_NAME" new-site "$BAKED_SITE" --admin-password "$BENCH_NAME-baked"
fi

# Take the baked site PAST the setup-wizard gate so a renamed clone serves the
# app at `/`, not a redirect to /setup-wizard (memory: fresh-site-setup-gate).
# The real gate is `Installed Application.is_setup_complete` for the frappe row
# (NOT just System Settings); set both. `bench frappe … execute` auto-commits.
# Baked here so deploy-site.py's rename path stays a pure move + password reset.
"$BENCH_CLI_DIR/bench" -b "$BENCH_NAME" frappe --site "$BAKED_SITE" execute \
	frappe.db.set_value \
	--args '["Installed Application", {"app_name": "frappe"}, "is_setup_complete", 1]'
"$BENCH_CLI_DIR/bench" -b "$BENCH_NAME" frappe --site "$BAKED_SITE" execute \
	frappe.db.set_single_value --args '["System Settings", "setup_complete", 1]'

# --- 6. Leave the bench STOPPED with the baked site in place. Assert the bake
# produced a working bench (frappe installed) — the build's own smoke test; the
# e2e re-asserts it over guest-SSH after the snapshot boots. ---
"$BENCH_CLI_DIR/bench" -b "$BENCH_NAME" list-apps

echo "Golden bench image baked: bench-cli @ ${BENCH_CLI_REF:0:12}, bench '${BENCH_NAME}' initialised with baked site '${BAKED_SITE}'."
