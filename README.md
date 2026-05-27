# Atlas

Atlas manages Firecracker virtual machines on servers. It is the lowest layer
of a Frappe hosting platform; sites, benches, IAM, and billing live in
separate apps on top.

- Spec: [spec/](./spec/README.md)
- Plan and history of how it got built: [plan/](./plan/00-overview.md)
- Spec/implementation drift: [plan/drift.md](./plan/drift.md)
- Shell scripts that run on the server: [scripts/](./scripts/README.md)

## What's here

- `atlas/` — the Frappe app source.
- `scripts/` — shell scripts uploaded over SSH and executed on the server.
- `spec/` — operator-facing specification.
- `plan/` — phased implementation plan (with `drift.md`).
- `llm/` — Claude-facing reference material.

## Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app atlas
```

## Local verification

After `bench install-app atlas` and creating an `atlas.local` site:

1. Put a DigitalOcean API token + SSH key fingerprint in the site config:

       bench --site atlas.local set-config -p atlas_do_token <DO_TOKEN>
       bench --site atlas.local set-config -p atlas_ssh_key_id <FINGERPRINT>
       bench --site atlas.local set-config -p atlas_ssh_private_key "$(cat ~/.ssh/atlas-test)"

2. Run the shared-droplet end-to-end suite:

       bench --site atlas.local execute atlas.tests.e2e.run_all

The run takes ~9 minutes and creates exactly one billable droplet (reused
across the shared-server use cases, deleted when the run ends). The
dedicated-droplet use cases own their own provision/teardown semantics and
are invoked directly:

       bench --site atlas.local execute atlas.tests.e2e.use_cases.digitalocean_client.run
       bench --site atlas.local execute atlas.tests.e2e.use_cases.server_provisioning.run
       bench --site atlas.local execute atlas.tests.e2e.use_cases.ssh_primitive.run

E2E tests are grouped by operator use case under [atlas/tests/e2e/use_cases/](./atlas/tests/e2e/use_cases/);
the going-forward guideline is in [plan/e2e-testing.md](./plan/e2e-testing.md).

## Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/atlas
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

## CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

## License

agpl-3.0
