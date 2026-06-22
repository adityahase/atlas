#!/usr/bin/env python3
# In-guest routing client for self-service subdomain routing (spec/18 Component D) —
# run INSIDE a bench VM, installed by build.sh at /usr/local/bin/atlas-route. The thin
# "push" half of the one-way model: the guest TELLS the controller what changed; the
# controller never reads the guest back. The controller stays the single authoritative
# writer of the fleet-wide-unique Subdomain table — this client only carries the
# guest's word, and every controller rule (uniqueness, reserved, denylist, per-VM cap,
# own-VM scoping) is arbitrated controller-side.
#
#   atlas-route register <label>     BEFORE `bench new-site` — the AUTHORITATIVE
#                                     reservation. Prints + exits non-zero on a Declined
#                                     (taken/reserved/at_limit/invalid) so the bench-cli
#                                     flow ABORTS before creating the local site
#                                     (block-at-create by ordering, no orphan).
#   atlas-route deregister <label>   AFTER `bench drop-site`, AND as the rollback when
#                                     `bench new-site` FAILS — best-effort, always exit 0.
#   atlas-route check-label <label>  OPTIONAL pre-flight — early UX feedback only, NOT
#                                     the gate. Non-zero on Declined, 0 on Available.
#   atlas-route list                 ON DEMAND — list this VM's routes, diff against
#                                     on-disk sites/, deregister each stray (per-stray).
#
# Caller resolution is by SOURCE ADDRESS — the client carries NO VM-identifying
# argument and the POST MUST go over IPv6 (the controller resolves the VM from the
# request's source /128). A v4 POST arrives NAT'd with no per-VM source, so the client
# pins the connection to AF_INET6 and treats "no v6 route to the controller" as a
# transport error, never a v4 retry.
#
# Identity is ONE non-secret file the controller injects (spec/18 "Identity"):
#   /etc/atlas-routing.env — ATLAS_BASE_URL=<trusted-edge base url> the guest POSTs to
# No VM UUID, no token: caller resolution is by source address, so the guest never sends
# a VM-identifying value. When the file is ABSENT the client raises NotConfigured and
# the wrapper no-ops cleanly, so an ordinary (non-Atlas) bench is unaffected.
#
# Stdlib only (the guest has no Atlas package). The bench-cli new_site/drop_site wiring
# (in the bench-cli repo) imports the typed surface below and branches on the result
# CLASS, never a status string or exit code.

import enum
import http.client
import json
import socket
import sys
import urllib.parse
from dataclasses import dataclass, field

ROUTING_ENV_PATH = "/etc/atlas-routing.env"

# Where the bench lays its sites out (the dir `bench setup nginx` scans). `list`'s
# stray finder diffs the controller's routes against the entry names here.
BENCH_SITES_DIRECTORY = "/home/frappe/bench-cli/benches/atlas/sites"

_METHOD = "atlas.atlas.bench_routing.{}"

# Short timeouts: register/check-label run inline in the interactive `bench new-site`
# flow (the user waits on the answer); deregister/list are best-effort/maintenance.
_TIMEOUT_SECONDS = 10


# --- the typed result surface (bench matches on the CLASS, not a string) ----------


@dataclass(frozen=True)
class Available:
	"""check_label ok — the label is free; `suffix` is the region domain to name with."""

	suffix: str


@dataclass(frozen=True)
class Registered:
	"""register ok — the name is reserved; `fqdn` is the controller-built FQDN."""

	label: str
	fqdn: str


@dataclass(frozen=True)
class Deregistered:
	"""deregister ok — the route is gone (or was already)."""

	label: str


@dataclass(frozen=True)
class Route:
	label: str
	fqdn: str
	active: bool


@dataclass(frozen=True)
class Listing:
	"""list ok — the caller VM's own routes."""

	domains: list = field(default_factory=list)


class Reason(enum.Enum):
	"""The controller's decline status strings, mirrored EXACTLY — so a new status
	can't slip through as an untyped string (an unknown wire status is a TransportError,
	not a silent pass)."""

	TAKEN = "taken"
	RESERVED = "reserved"
	AT_LIMIT = "at_limit"
	INVALID = "invalid"


@dataclass(frozen=True)
class Declined:
	"""A declined write/check — NOT an exception: an expected business outcome the
	caller branches on. `message` is the operator-facing text, verbatim where the
	controller gave one."""

	reason: Reason
	message: str


# --- typed failures (the caller decides fatal vs best-effort) ---------------------


class RoutingError(Exception):
	"""Base for every routing-client failure."""


class NotConfigured(RoutingError):
	"""/etc/atlas-routing.env absent — the no-op signal (not an Atlas-routed bench)."""


class TransportError(RoutingError):
	"""Unreachable / no v6 route / timeout / bad JSON / an unknown wire status."""


# --- the IPv6-only HTTP transport -------------------------------------------------


class _IPv6HTTPConnection(http.client.HTTPConnection):
	"""Force the connection to the IPv6 address family. Caller resolution matches the
	request's source /128 against Virtual Machine.ipv6_address, so the POST MUST reach
	the controller over IPv6 — a v4 POST arrives NAT'd with no per-VM source to resolve.
	We resolve the host to its AAAA and connect over AF_INET6 only; if there is no v6
	route (no AAAA / connect fails), that is a TransportError, NEVER a v4 fallback."""

	def connect(self) -> None:
		try:
			infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET6, socket.SOCK_STREAM)
		except OSError as error:
			raise TransportError(f"no IPv6 route to {self.host}:{self.port} ({error})") from error
		if not infos:
			raise TransportError(f"controller {self.host} has no AAAA (IPv6) address")
		last: Exception | None = None
		for _family, socktype, proto, _canon, sockaddr in infos:
			try:
				self.sock = socket.socket(socket.AF_INET6, socktype, proto)
				self.sock.settimeout(self.timeout)
				self.sock.connect(sockaddr)
				return
			except OSError as error:
				last = error
				if self.sock:
					self.sock.close()
		raise TransportError(f"could not connect to {self.host} over IPv6 ({last})")


class _IPv6HTTPSConnection(http.client.HTTPSConnection):
	"""The TLS variant — same AF_INET6-only connect, wrapped in the SSL context the base
	class builds. (Production posts to the trusted-edge FQDN over https.)"""

	def connect(self) -> None:
		try:
			infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET6, socket.SOCK_STREAM)
		except OSError as error:
			raise TransportError(f"no IPv6 route to {self.host}:{self.port} ({error})") from error
		if not infos:
			raise TransportError(f"controller {self.host} has no AAAA (IPv6) address")
		last: Exception | None = None
		for _family, socktype, proto, _canon, sockaddr in infos:
			sock = None
			try:
				sock = socket.socket(socket.AF_INET6, socktype, proto)
				sock.settimeout(self.timeout)
				sock.connect(sockaddr)
				# `self._context` is HTTPSConnection's default SSL context, which verifies
				# the cert + hostname — the trust-root transport posts to the edge FQDN.
				self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
				return
			except OSError as error:
				# ssl.SSLError subclasses OSError, so a TLS handshake failure lands here
				# too — close the connected socket so a failed handshake never leaks an fd.
				last = error
				if sock:
					sock.close()
		raise TransportError(f"could not connect to {self.host} over IPv6 ({last})")


def _read_base_url() -> str:
	"""The controller base URL from /etc/atlas-routing.env (ATLAS_BASE_URL=…). Raises
	NotConfigured when the file is absent/blank — the signal that makes the wrapper
	no-op cleanly (this is not an Atlas-routed bench)."""
	try:
		with open(ROUTING_ENV_PATH) as handle:
			content = handle.read()
	except OSError as error:
		raise NotConfigured(f"{ROUTING_ENV_PATH} absent ({error})") from error
	for line in content.splitlines():
		line = line.strip()
		if line.startswith("ATLAS_BASE_URL="):
			value = line.split("=", 1)[1].strip()
			if value:
				return value
	raise NotConfigured(f"{ROUTING_ENV_PATH} has no ATLAS_BASE_URL")


def _post(base_url: str, method: str, params: dict) -> dict:
	"""POST to a whitelisted Frappe method over IPv6 and return its `message` payload.

	Frappe wraps a whitelisted return value as `{"message": <value>}`; we unwrap it.
	Form-encoded body, so `frappe.form_dict` and the @rate_limit key see the params
	exactly as the SPA/signup paths send them. Every transport-level failure (no v6
	route, timeout, non-2xx, bad JSON) is a TransportError the caller decides on."""
	parsed = urllib.parse.urlsplit(base_url)
	host = parsed.hostname
	if not host:
		raise TransportError(f"malformed ATLAS_BASE_URL {base_url!r}")
	if parsed.scheme == "https":
		port = parsed.port or 443
		connection = _IPv6HTTPSConnection(host, port, timeout=_TIMEOUT_SECONDS)
	else:
		port = parsed.port or 80
		connection = _IPv6HTTPConnection(host, port, timeout=_TIMEOUT_SECONDS)
	body = urllib.parse.urlencode(params)
	path = f"/api/method/{urllib.parse.quote(_METHOD.format(method), safe='.')}"
	try:
		connection.request(
			"POST",
			path,
			body=body,
			headers={
				"Content-Type": "application/x-www-form-urlencoded",
				"Accept": "application/json",
				"Host": host,
			},
		)
		response = connection.getresponse()
		raw = response.read().decode()
		if response.status >= 400:
			raise TransportError(f"{method} HTTP {response.status}: {raw[:300]}")
		payload = json.loads(raw)
	except TransportError:
		raise
	except (OSError, ValueError) as error:
		raise TransportError(f"{method} failed ({error})") from error
	finally:
		connection.close()
	return payload.get("message", payload) if isinstance(payload, dict) else payload


def _decline(result: dict) -> Declined:
	"""Map a controller status dict to a typed Declined, or raise TransportError on an
	unknown status — a new status can't slip through as an untyped pass."""
	status = (result or {}).get("status")
	try:
		reason = Reason(status)
	except ValueError as error:
		raise TransportError(f"unknown controller status {status!r}") from error
	message = (result or {}).get("reason") or _default_message(reason, result)
	return Declined(reason=reason, message=message)


def _default_message(reason: Reason, result: dict) -> str:
	label = (result or {}).get("_label", "the subdomain")
	if reason is Reason.TAKEN:
		return f"subdomain {label} is already in use — choose another"
	if reason is Reason.RESERVED:
		return f"subdomain {label} is reserved — choose another"
	if reason is Reason.AT_LIMIT:
		return "this VM has reached its subdomain limit — drop a site or use a bigger VM"
	return f"subdomain {label} is not a valid label"


# --- the typed client surface the bench-cli wiring imports ------------------------


def register(label: str) -> "Registered | Declined":
	"""POST register(label) — the authoritative reservation, BEFORE `bench new-site`.
	Returns Registered on ok, Declined on taken/reserved/at_limit/invalid. Raises
	NotConfigured (no-op signal) / TransportError. Idempotent on the caller's own
	label, so a retry after a transient TransportError is safe."""
	base_url = _read_base_url()
	result = _post(base_url, "register", {"label": label})
	if (result or {}).get("status") == "ok":
		# register's ok echoes the region suffix, so the FQDN needs no second round-trip.
		suffix = (result or {}).get("suffix")
		fqdn = f"{label}.{suffix}" if suffix else label
		return Registered(label=label, fqdn=fqdn)
	return _decline({**result, "_label": label})


def deregister(label: str) -> Deregistered:
	"""POST deregister(label) — best-effort teardown / create-failure rollback. Returns
	Deregistered. Raises NotConfigured / TransportError (the caller treats both as
	non-fatal on the drop path — a lost deregister leaves a 404-serving stale route the
	owner clears later via `list`)."""
	base_url = _read_base_url()
	_post(base_url, "deregister", {"label": label})
	return Deregistered(label=label)


def check_label(label: str) -> "Available | Declined":
	"""POST check_label(label) — OPTIONAL pre-flight, early UX feedback only (NOT the
	gate). Returns Available(suffix) on ok, Declined otherwise. Raises NotConfigured /
	TransportError."""
	base_url = _read_base_url()
	result = _post(base_url, "check_label", {"label": label})
	if (result or {}).get("status") == "ok":
		return Available(suffix=(result or {}).get("suffix", ""))
	return _decline({**result, "_label": label})


def list_routes() -> Listing:
	"""POST list() — the caller VM's own routes. Returns Listing(domains=[Route, ...]).
	Raises NotConfigured / TransportError. (Named `list_routes`, not `list`, so the
	module doesn't shadow the builtin guest-side.)"""
	base_url = _read_base_url()
	result = _post(base_url, "list", {})
	routes = [
		Route(label=row.get("label", ""), fqdn=row.get("fqdn", ""), active=bool(row.get("active")))
		for row in (result or {}).get("domains", [])
	]
	return Listing(domains=routes)


# --- the CLI wrapper (thin; the bench-cli wiring prefers the typed surface) -------


def _cmd_register(label: str) -> int:
	"""register before new-site. Non-zero on Declined so the bench-cli flow ABORTS before
	creating the local site. NotConfigured → 0 (not an Atlas bench). TransportError → 0
	(fail-open: a momentarily-unreachable controller must not block a local create; the
	authoritative uniqueness is still the controller's atomic insert)."""
	try:
		outcome = register(label)
	except NotConfigured as error:
		print(f"atlas-route: no routing config ({error}); skipping register", file=sys.stderr)
		return 0
	except TransportError as error:
		print(f"atlas-route: register unreachable ({error}); not blocking", file=sys.stderr)
		return 0
	if isinstance(outcome, Registered):
		print(f"atlas-route: reserved {outcome.fqdn}", file=sys.stderr)
		return 0
	print(f"atlas-route: {outcome.message}", file=sys.stderr)
	return 2


def _cmd_deregister(label: str) -> int:
	"""deregister after drop / as rollback — best-effort, ALWAYS exit 0."""
	try:
		deregister(label)
	except NotConfigured:
		return 0
	except TransportError as error:
		print(f"atlas-route: deregister failed ({error}); the owner can clear via list", file=sys.stderr)
	return 0


def _cmd_check_label(label: str) -> int:
	"""check-label pre-flight — non-zero on Declined, 0 on Available / no-config /
	unreachable (fail-open, the same rules as register; it is NOT the gate)."""
	try:
		outcome = check_label(label)
	except NotConfigured:
		return 0
	except TransportError as error:
		print(f"atlas-route: check-label unreachable ({error}); not blocking", file=sys.stderr)
		return 0
	if isinstance(outcome, Available):
		return 0
	print(f"atlas-route: {outcome.message}", file=sys.stderr)
	return 2


def _cmd_list() -> int:
	"""list — enumerate this VM's routes, diff against on-disk sites/, deregister each
	stray (per-stray, never bulk). Maintenance subcommand; always exit 0."""
	try:
		listing = list_routes()
	except NotConfigured:
		return 0
	except TransportError as error:
		print(f"atlas-route: list unreachable ({error})", file=sys.stderr)
		return 0
	on_disk = _on_disk_sites()
	for route in listing.domains:
		print(f"atlas-route: routed {route.fqdn} (active={route.active})", file=sys.stderr)
		if route.fqdn not in on_disk and route.label not in on_disk:
			print(f"atlas-route: stray {route.fqdn} (no on-disk site); deregistering", file=sys.stderr)
			_cmd_deregister(route.label)
	return 0


def _on_disk_sites() -> set:
	"""The bench's on-disk site directory names. A routed label with no match here is a
	stray. Returns an empty set when the dir is unreadable (then `list` clears nothing —
	the conservative branch)."""
	import os

	try:
		return set(os.listdir(BENCH_SITES_DIRECTORY))
	except OSError:
		return set()


def main(argv: list) -> int:
	if len(argv) == 3 and argv[1] == "register":
		return _cmd_register(argv[2])
	if len(argv) == 3 and argv[1] == "deregister":
		return _cmd_deregister(argv[2])
	if len(argv) == 3 and argv[1] == "check-label":
		return _cmd_check_label(argv[2])
	if len(argv) == 2 and argv[1] == "list":
		return _cmd_list()
	print(
		"usage: atlas-route register <label> | deregister <label> | check-label <label> | list",
		file=sys.stderr,
	)
	return 64  # EX_USAGE


if __name__ == "__main__":
	sys.exit(main(sys.argv))
