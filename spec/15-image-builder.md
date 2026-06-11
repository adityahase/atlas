# Image Builder

Two artifacts in Atlas are built the same way: **build a script inside a plain
guest over SSH, then snapshot the result.** The golden bench image
([08-images.md § golden bench image](./08-images.md)) and the reverse-proxy image
([12-proxy.md](./12-proxy.md)) are both produced this way. This chapter is the
**Image Builder**: the operator-facing layer that owns that bake — provision a
scratch VM, run the recipe's `build.sh` in it, snapshot it, optionally register
the snapshot — under one DocType, one button, one audit trail, and one code path.

Before this layer the two bakes lived **out of band**: the build verbs
(`bench_image.build_bench`, `proxy.build_proxy`) were near-identical duplicates
driven only from e2e test modules, with the provision→build→snapshot
orchestration hand-rolled in e2e helpers. There was no operator button, no row
recording *"this snapshot was baked from this recipe,"* and no place for a third
image type to land without a third copy of the build verb. This layer removes all
three gaps.

## The shape

Three pieces, smallest surface that removes the duplication and gives the operator
a button:

```
   Image Recipe registry (code)          Image Build (DocType, operator)
   ────────────────────────────          ───────────────────────────────
   bench  → bench/  build.sh             one row per bake run
   proxy  → proxy/  build.sh             status: Draft → Provisioning →
   (more later)                            Building → Snapshotting →
            │                              Available / Failed
            │  get_recipe(name)            │
            ▼                              ▼  after_insert → enqueue run()
   atlas.atlas.image_builder.run_build(vm, recipe)   ◄── shared seam
   upload tree · run_detached(build.sh) · finalize hook · one Task row
            │
            ▼
   Virtual Machine Snapshot  ──▶  Atlas Settings.default_bench_snapshot
   (the rollable artifact)        (bench) / proxy fleet clone source
```

What this layer is **not**: it does not replace the committed `bench/` and
`proxy/` trees or their `build.sh` scripts — those stay the source of truth for
*what gets installed* (spec taste #15). It owns the **controller-side lifecycle**:
provision, upload, run, snapshot, register, audit. A recipe just *names* an
existing committed tree.

## The recipe registry (code-defined)

[`atlas/atlas/image_recipes.py`](../atlas/atlas/image_recipes.py) is a frozen
`ImageRecipe` dataclass registry (`RECIPES`), keyed by a short recipe name. It is
**code, not a DocType** — a recipe points entirely at committed files and pinned
sizes, and its `finalize` is a callback, so a data row could only mirror it. This
is the same call the spec makes for `sizes.py SIZE_PRESETS` (the canonical source,
mirrored into JS/SPA) and the `DEFAULT_IMAGE` constants in `bootstrap.py`. Adding
an image type is a small reviewable code change beside the tree it bakes — the
same discipline the two `build.sh` files' pinned versions follow.

Each `ImageRecipe` declares: the committed `source_directory` (uploaded verbatim),
the `build_entrypoint` run over guest-SSH, the build-VM sizing
(`vcpus`/`memory_megabytes`/`disk_gigabytes`), the `snapshot_title` stamped on the
output, the `task_script` name for the audit row, top-level `exclude` entries (the
proxy's dev-only `test/` harness), a `finalize` callback, a `registers_as` Atlas
Settings field, `is_proxy`, and an optional `warm_entrypoint` (the in-guest script
a **warm bake** runs before the paused capture — see *The warm bake* below; empty
means the recipe only bakes cold). Two recipes ship:

| Recipe | Tree | Build VM | Snapshot | Special |
| ------ | ---- | -------- | -------- | ------- |
| `bench` | `bench/` | 2 vCPU / 2 GB / 12 GB | `golden-bench` | `registers_as = default_bench_snapshot`, `warm_entrypoint = warm.sh` |
| `proxy` | `proxy/` | 2 vCPU / 1 GB / 10 GB | `proxy-image` | `exclude = ("test",)`, `finalize = _finalize_proxy`, `is_proxy` |

The recipe **subsumes the per-module constants** that used to live in the build
verbs and the e2e modules (`GOLDEN_DISK_GB`, `GOLDEN_MEMORY_MB`,
`REMOTE_*_DIRECTORY`, the `test/` exclude, the proxy finalize block). `finalize`
is a callback because the proxy's post-build step (write `REGION_FILE`,
`systemctl restart atlas-proxy.service`, [`_finalize_proxy`](../atlas/atlas/image_recipes.py))
is genuinely code; the bench recipe has `finalize = None`. `registers_as` lets a
successful bench bake auto-set `Atlas Settings.default_bench_snapshot` (the field
self-serve already reads); proxy snapshots feed a fleet, not a Single, so they
have no `registers_as`.

## The shared builder seam

[`atlas/atlas/image_builder.py`](../atlas/atlas/image_builder.py)'s
`run_build(virtual_machine, recipe, on_task=None)` is the de-duplicated core the
two build verbs collapse into. It:

1. `connection_for_guest(vm)` + `forget_host(host)` — the recycled-IP host-key
   trap (real-provision-traps #1); this path goes straight to scp/ssh with no
   `wait_for_ssh`, so a stale pinned key must be dropped first.
2. `tree_uploads(recipe)` — enumerate the committed tree (`rglob`, skipping
   `recipe.exclude` and `__pycache__`), then `mkdir -p` + `run_scp` every file
   under one staging dir so `build.sh` finds its siblings.
3. `run_detached(build.sh, log, done)` — run the long build (apt/clone/uv for
   bench, an nginx+luajit compile for proxy) **detached**, so a mid-build SSH
   reset doesn't SIGHUP it; poll the marker. ([04-tasks.md](./04-tasks.md),
   `transport.run_detached`.)
4. `recipe.finalize(vm, connection, key_path)` — the post-build guest step, if
   the recipe has one. Its exit status becomes the build's, so a finalize failure
   is a build failure.
5. `_record_guest_task(...)` — one Task row (named by `recipe.task_script`,
   `bench-build` / `proxy-build`) for the audit trail, the same row shape as every
   guest op. `on_task`, if given, is called with the Task name **before** the
   throw, so the Image Build controller links the build Task even on failure.
6. `frappe.throw` on any non-zero exit — fail loud at the boundary (spec taste
   #17); the operator retries by clicking.

`bench_image.build_bench` and `proxy.build_proxy` are now thin wrappers over
`run_build` (proxy keeps its `is_proxy`/`region` guards). Their public signatures
are unchanged, so `bootstrap.py`, the e2e modules, and any caller keep working.
`proxy.py` keeps `reconcile_*`, `push_cert`, `canonical_json`,
`wildcard_targets_for_region`, and `_record_guest_task` (now returning the Task
name) — only the upload/build half of `build_proxy` moved.

## The `Image Build` DocType

The operator-facing object: one row per bake run, named `IMG-BUILD-#####`. It is
**operator-only** — `Image Build` carries only a System Manager permission and is
not in `_OWNED_DOCTYPES`, so it is invisible and access-denied to the SPA `Atlas
User`, like `Provider` / `Server` / `Task`. Baking images is an operator-fleet
operation, not a per-user one.

Fields and the full table are in
[02-doctypes.md → Image Build](./02-doctypes.md#image-build). The identity tuple
(`recipe`, `server`, `region`, `base_image`) is `set_only_once` and guarded in
`validate()` — re-baking with a different recipe/server/base is a new row, not an
in-place edit (the same shape as `Site` / `Virtual Machine`).

### Lifecycle

1. **`before_insert`** resolves the recipe, copies its `title`, defaults
   `base_image` from `placement.default_image()`, requires a `region` for an
   `is_proxy` recipe, and starts `Draft`. The build VM is created in the
   background job, not here — provisioning SSHes and must not block the insert.
2. **`after_insert`** enqueues `run` on `queue="long"` (it SSHes and waits
   ~10–20 min — the same queue `Site.auto_provision` and image-sync use). No-op if
   not `Draft`.
3. **`run(image_build_name)`** — the background orchestration. The part that used
   to live only in e2e helpers, now first-class:

   | Step | Action | Status |
   | ---- | ------ | ------ |
   | 1 | Provision a scratch build VM at the recipe's size on `server` from `base_image` (an `is_proxy` recipe stamps `is_proxy` + `region`). **Commit**, then wait for its own after_insert provision job to reach Running. | `Provisioning` |
   | 2 | `run_build(vm, recipe)` — upload the tree + run `build.sh` in the guest (+ finalize). Links the `build_task`. | `Building` |
   | 3 | Cold (default): stop the build VM and `snapshot(title=recipe.snapshot_title)`. **Warm** (`warm` checked): run the warm finalize instead — see below. Link it into `snapshot`. | `Snapshotting` → `Available` |
   | 4 | If `auto_register` and the recipe has `registers_as`, write the snapshot into that Atlas Settings field. | (still `Available`) |
   | 5 | If `terminate_build_vm`, terminate the scratch build VM. | |

   Any failure flips `status = Failed`, records the stderr tail in `error`, and
   re-raises (fail loud — the job log carries the traceback). No-op if the build
   has moved past `Draft`. Every transition is committed and pushed to the
   operators' realtime room (`image_build_progress`, doc-scoped) so the desk
   form's live checklist updates without a reload — the `Site.auto_provision` /
   `/site-status` pattern ([14-self-serve.md](./14-self-serve.md)) applied to a
   desk form.

4. **`rebake()`** resets an `Available`/`Failed` row to `Draft` and re-enqueues —
   the operator's retry button. The whole pipeline is idempotent (`build.sh`
   re-runs cleanly, a re-bake reuses a surviving build VM), so retry = re-run
   (spec taste #16).

The **commit-before-wait** in step 1 is load-bearing and copied from
`Site.auto_provision`: the build VM's own `after_insert` enqueued its boot job in a
**separate** transaction that can't run until this one commits. Holding the
transaction open and blocking on the wait would deadlock the boot, time out, and
roll back the VM row — orphaning its boot job.

### The warm bake (`warm`)

A bench bake with **`warm`** checked produces a `kind=Warm`
`Virtual Machine Snapshot` — the fan-out golden of
[05-virtual-machine-lifecycle.md → Warm snapshot fan-out](./05-virtual-machine-lifecycle.md#warm-snapshot-fan-out-one-golden-n-restored-clones):
clones of it **resume** a pre-warmed, already-serving guest instead of booting
one. Where the cold golden's contract is "bench installed, everything
stopped", the warm bake's is the opposite — whatever is resident in the
guest's RAM at the pause is exactly what every clone wakes into. Step 3
becomes:

1. **Arm the guest** — run the recipe's `warm_entrypoint` (`bench/warm.sh`)
   over guest-SSH, recorded as a `bench-warm` Task. It installs and starts the
   **identity freshen unit** (`atlas-warm-freshen`, which must be alive
   mid-loop at the capture instant), runs `bench setup production` against the
   baked `site.local` **with `listen [::]:80;` added to the vhosts** (the
   clone is probed and served over its /128 — a v4-only frozen nginx fails
   every real probe), **pre-warms with real localhost HTTP on both families**
   (an Administrator login + `/app`, `/login`, pings — so gunicorn workers,
   the MariaDB buffer pool, compiled assets and bootinfo are resident in the
   RAM about to be frozen), deletes the systemd random-seed (clone-entropy
   hygiene), and ends with a **`sync`**: the disk snapshot below is
   crash-consistent, so anything still dirty in the page cache would exist in
   the frozen RAM (restores see it) but not on the captured disk — the
   cold-boot fallback would boot a guest with no freshen unit and never become
   reachable (proven on a real host).
2. **Capture at one paused instant** — `warm-snapshot-vm.py` pauses the vCPUs,
   `PUT /snapshot/create`s the memory pair, takes the LVM thin snapshot of the
   disk **while still paused** (the pair is only valid together), moves the
   pair to the durable `/var/lib/atlas/snapshots/<name>/`, records the host
   signature beside it, and resumes. Fail-loud — a bake step, not an
   opportunistic fast path.
3. **Register + supersede** — the row captures the machine config (vcpus,
   memory) and tap name the vmstate pins; older Warm rows on the same server
   are trashed (one current warm golden per server; their `on_trash` removes
   the LV + memory directory). Then the build VM is stopped — the warmth lives
   in the artifact, not the scratch VM.

Only recipes with a `warm_entrypoint` can bake warm (`before_insert` rejects
the rest); today that is `bench` only. `auto_register` applies as usual: the
warm row is also a perfectly good **cold** golden (its disk carries the baked
site + production config), so registering it as `default_bench_snapshot`
gives one row both roles — the per-server warm resolution and the
single-value cold fallback stay distinct *concepts* either way
([14-self-serve.md](./14-self-serve.md)).

### The build VM is scratch; the snapshot is durable

The **snapshot is the output**; the build VM is scratch. By default
`terminate_build_vm` is **off**, so the build VM is left Stopped for re-bake or
inspection (the e2e's historical behavior) — "scratch" means disposable, not
auto-deleted. The snapshot is a durable artifact that outlives its build VM:
self-serve sites and the proxy fleet clone from it indefinitely via
`Virtual Machine Snapshot.clone_to_new_vm`, which takes the clone's `server` from
the snapshot's own row, not the (possibly-gone) build VM (see
[14-self-serve.md](./14-self-serve.md) and [08-images.md](./08-images.md)).

## Entry points

- **`Image Build` → New** in Desk, or **`Server` → Bake Image** (an `Actions ▾`
  item on an Active server, parity with **Sync Image**) — opens a dialog that
  inserts an `Image Build` on that server and routes to its live-checklist form.
- **`Image Build` → Re-bake** on an Available/Failed row.

## Design decisions

A few choices that aren't obvious from the field list:

- **The recipe is code, not a DocType.** A recipe points entirely at committed
  files (the `bench/` / `proxy/` tree, the pinned `build.sh`) and a `finalize`
  callback, so a data row could only mirror it — the same call `sizes.py
  SIZE_PRESETS` and the `bootstrap.py` image constants already make. A third image
  type is a recipe entry plus a committed tree, no new module.
- **Region is asked, not derived.** A proxy build takes its `region` from the
  dialog (required for an `is_proxy` recipe) rather than reading it off the server.
  Simpler than threading server→region, and it lets a build target a region label
  directly.
- **Distinct Task script names.** The audit Task keeps the per-recipe name
  (`bench-build` / `proxy-build`, via `recipe.task_script`) rather than one generic
  `image-build`, so the operator's Task list stays readable.
- **No snapshot back-link.** Provenance rides the `Image Build.snapshot` forward
  link only; `Virtual Machine Snapshot` stays frozen. A `Virtual Machine
  Snapshot.image_build` back-link is a cheap future add if "what baked this
  snapshot?" from the snapshot side becomes a real need.
- **No concurrency lock.** A second `Image Build` on a busy server just provisions
  another VM. Two bakes of the same recipe racing to `auto_register` the same Atlas
  Settings field is last-writer-wins (acceptable).

## Testing

- **Unit (milliseconds):**
  - *Recipe registry + seam* — the recipe shapes, the tree enumeration with
    `exclude`/`__pycache__` filtering, the `run_build` upload→detached-build→Task
    path (SSH plumbing mocked), the `on_task` callback firing before the throw,
    fail-loud, and the proxy finalize running after the build. See
    [`atlas/atlas/test_image_builder.py`](../atlas/atlas/test_image_builder.py).
  - *Controller* — `before_insert` defaults + the region requirement,
    immutability, the `run()` state machine (status transitions, artifact
    linking, auto-register on/off, terminate on/off, fail-loud, the
    not-`Draft` no-op), and `rebake`. Host steps mocked at the module seams. See
    [`atlas/atlas/doctype/image_build/test_image_build.py`](../atlas/atlas/doctype/image_build/test_image_build.py).
  - The two build verbs keep their own thin coverage of what they still own —
    `build_proxy`'s `is_proxy`/`region` guards
    ([`test_proxy.py`](../atlas/atlas/test_proxy.py)) and `build_bench`'s
    delegation ([`test_bench_image.py`](../atlas/atlas/test_bench_image.py)).
- **Host facts (e2e):** the bake's host facts — a baked VM has a working `bench`
  over guest-SSH ([`bench_image.py`](../atlas/tests/e2e/use_cases/bench_image.py)),
  the proxy compiles and serves ([`proxy_vm.py`](../atlas/tests/e2e/use_cases/proxy_vm.py)) —
  are unchanged; they exercise the same `build_bench`/`build_proxy` verbs, which
  now route through `run_build`. Driving those e2e modules through the `Image
  Build` DocType (insert a row, assert it reaches `Available`) rather than the
  bare build verbs is a follow-up, host-verifiable on a real droplet.
