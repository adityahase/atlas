"""End-to-end tests grouped by operator use case.

Each module exercises one operator-visible operation against a real
DigitalOcean droplet. The module name is the use case; the body covers the
happy path, the negative paths the operator can hit on the same use case,
and the validation throws that guard the same DocType methods.

Modules that need an Active server take `(reuse=True, keep=True)` and run
inside `_shared.phase()`; modules that bring their own droplet or none at
all do not.

[run_all](../__init__.py) orchestrates the shared-droplet use cases against
one billable droplet. The dedicated-droplet modules
([digitalocean_client](./digitalocean_client.py),
[server_provisioning](./server_provisioning.py)) are invoked directly:

    bench --site atlas.local execute atlas.tests.e2e.use_cases.digitalocean_client.run
    bench --site atlas.local execute atlas.tests.e2e.use_cases.server_provisioning.run
"""
