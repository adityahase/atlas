# Phase 2 — DigitalOcean API client

## Goal

A tiny `requests`-based HTTP client for the four DigitalOcean endpoints Atlas
actually needs. No DocTypes. No buttons. Just a callable Python module.

This client gets used by:

- Phase 3 (`Server Provider.Provision Server` button) — create + poll droplet.
- Phase 3 (`Server Provider.Test Connection`) — account ping.
- Every phase's e2e — create + delete throwaway droplets.

## You can do this at the end

```python
# bench --site atlas.local console
from atlas.atlas.digitalocean import DigitalOceanClient

client = DigitalOceanClient(token="dop_v1_...")
client.account()  # raises if token bad

droplet = client.create_droplet(
    name="atlas-e2e-12345",
    region="blr1",
    size="s-2vcpu-4gb-intel",
    image="ubuntu-24-04-x64",
    ssh_key_ids=["12:34:56:..."],
    tags=["atlas-e2e"],
    ipv6=True,
)
droplet = client.wait_for_active(droplet["id"], timeout_seconds=300)
print(droplet["networks"]["v4"][0]["ip_address"])
print(droplet["networks"]["v6"][0]["ip_address"])
client.delete_droplet(droplet["id"])
```

## Files added

- `atlas/atlas/atlas/digitalocean.py` — the client. ~120 lines.
- `atlas/atlas/tests/test_digitalocean.py` — unit tests with recorded fixtures.
- `atlas/atlas/tests/fixtures/digitalocean/` — JSON fixtures.
- `atlas/atlas/tests/e2e/phase_2.py` — real-droplet round trip.

## Module surface

```python
class DigitalOceanError(Exception): ...

class DigitalOceanClient:
    def __init__(self, token: str, base_url: str = "https://api.digitalocean.com/v2"): ...

    def account(self) -> dict:
        """GET /account. Raises if token invalid."""

    def create_droplet(
        self,
        *,
        name: str,
        region: str,
        size: str,
        image: str,
        ssh_key_ids: list[str],
        tags: list[str],
        ipv6: bool = True,
    ) -> dict:
        """POST /droplets. Returns the partial droplet dict from the response."""

    def get_droplet(self, droplet_id: int) -> dict:
        """GET /droplets/{id}."""

    def wait_for_active(self, droplet_id: int, timeout_seconds: int = 300) -> dict:
        """Poll get_droplet every 5s until status='active', then return.
        Raises DigitalOceanError on timeout."""

    def delete_droplet(self, droplet_id: int) -> None:
        """DELETE /droplets/{id}. 204 or 404 are both fine."""

    def list_droplets_by_tag(self, tag: str) -> list[dict]:
        """GET /droplets?tag_name=... — used by the e2e pre-sweep."""
```

That's it. No regions/sizes endpoints; the operator types those into the
provider form. No image lookup; same story.

### Implementation notes

- Use `frappe.utils.requests` if it exists, otherwise stdlib `requests`. Most
  Frappe apps just `import requests`. Match that.
- All methods raise `DigitalOceanError` on 4xx/5xx with the response body.
- `Authorization: Bearer <token>` on every request.
- 30-second timeout on every request.
- No retry logic in this iteration (no flaky-network mitigation). One shot.

### Parsing the addresses

After `wait_for_active`, callers extract:

- IPv4: `droplet["networks"]["v4"][0]["ip_address"]` (the first public v4).
- IPv6: the v6 entry with `type == "public"`. Helper:

```python
def public_ipv6(droplet: dict) -> tuple[str, str]:
    """Returns (host_address, prefix_cidr) e.g. ('2a03:b0c0:abcd:1234::1', '2a03:b0c0:abcd:1234::/64')."""
```

The /124 carve-out (per [`../spec/06-networking.md`](../spec/06-networking.md))
is a phase-3 concern; this client just returns what DO gave us.

## Test plan

### Unit tests (`atlas/atlas/tests/test_digitalocean.py`)

Mock `requests.request` (single chokepoint inside the client). Use recorded
fixtures stored as JSON under `fixtures/digitalocean/`:

- `test_account_ok`, `test_account_bad_token`.
- `test_create_droplet_request_shape`: asserts the JSON body matches DO's
  documented schema (name, region, size, image, ssh_keys, ipv6, tags).
- `test_wait_for_active_polls_until_active`: feed the mock a sequence
  `[status=new, status=new, status=active]`, assert 3 calls.
- `test_wait_for_active_times_out`: status=new for every call, assert raise.
- `test_delete_droplet_treats_404_as_success`.
- `test_public_ipv6_from_droplet_fixture`: assert
  `('2a03:b0c0:abcd:1234::1', '2a03:b0c0:abcd:1234::/64')`.

### E2E (`atlas/atlas/tests/e2e/phase_2.py`)

Needs `DO_TOKEN` and `DO_SSH_KEY_ID` in site config.

1. Pre-sweep tagged `atlas-e2e` droplets > 30 min.
2. Create one droplet tagged `atlas-e2e,phase-2`.
3. `wait_for_active`. Assert v4 + v6 addresses returned.
4. `get_droplet`. Assert status=active.
5. `delete_droplet`. Assert subsequent `get_droplet` 404s within 60s.
6. `finally`: defensively `delete_droplet` again.

Bench invocation:

```
bench --site atlas.local execute atlas.tests.e2e.phase_2.run
```

## Shared e2e helpers (`tests/e2e/_shared.py`)

Land here, used by all subsequent phases:

```python
TAG = "atlas-e2e"
SWEEP_AGE_SECONDS = 30 * 60

def get_client() -> DigitalOceanClient: ...
def get_ssh_key_id() -> str: ...
def get_ssh_private_key() -> str: ...   # reads from site config
def sweep_old_droplets(client) -> None: ...
def create_test_droplet(client, name_suffix: str) -> dict: ...
def cleanup_droplet(client, droplet_id: int) -> None: ...
```

`name_suffix` lets each phase tag its own droplets (e.g. `phase-3-bootstrap`)
for easier debugging.

## What we are NOT doing in this phase

- No DocType yet (phase 3).
- No region/size validation. Operator types whatever DO accepts.
- No retry on transient 5xx.
- No webhook handling. We poll.
- No project assignment. Default DO project.
- No SSH key creation (operator uploads to DO out-of-band).

## Spec drift introduced

None. The spec doesn't describe the client beyond "use requests."
