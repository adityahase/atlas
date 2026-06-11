# Image Builder — design

**Status:** built — the durable spec is [`spec/15-image-builder.md`](../spec/15-image-builder.md)
(this file is the design rationale behind it, the sibling of the
[TLS design, now folded into `spec/13-tls.md`](../spec/13-tls.md)). This
was the design interview for an abstraction that turns *"build an image by running a
script inside a guest over SSH, then snapshot it"* into a first-class,
Atlas-tracked operator operation, folding the two bakes that lived **out of band** —
the golden bench image and the proxy image — under one DocType, one set of buttons,
one audit trail, and one code path. It shipped close to this plan; the §9 deltas
below note where the build diverged.

---

## 1. The problem

Atlas already knows how to **build an image inside a running guest** and turn the
result into a rollable artifact. It does it twice, identically, and **neither is
an operator-visible operation** — both live only in e2e test modules:

| Image | Build verb (controller) | Driven from | Recipe (source tree) | Output |
| ----- | ----------------------- | ----------- | -------------------- | ------ |
| Golden bench | `atlas.atlas.bench_image.build_bench(vm)` | `tests/e2e/use_cases/bench_image.py` | committed `bench/` + `bench/build.sh` | a `Virtual Machine Snapshot` (clone source for site VMs) |
| Proxy | `atlas.atlas.proxy.build_proxy(vm)` | `tests/e2e/use_cases/proxy_vm.py` | committed `proxy/` + `proxy/build.sh` | a `Virtual Machine Snapshot` (clone source for proxy VMs) |

The two `build_*` functions are **near-identical** (compare
[`bench_image.py`](../atlas/atlas/bench_image.py) and `build_proxy` in
[`proxy.py`](../atlas/atlas/proxy.py)):

1. `connection_for_guest(vm)` + `forget_host` (recycled-IP host-key trap).
2. Enumerate a committed source tree → `(local, remote)` upload pairs.
3. `mkdir -p` the remote dirs, `run_scp` every file.
4. `run_detached(build.sh, log, done)` — the long build, immune to SSH reset.
5. `_record_guest_task(...)` — one synthetic Task row for the audit trail.
6. `frappe.throw` on non-zero exit.

The **orchestration around** the build (provision a build VM → build → stop →
snapshot → register/teardown) lives only in the e2e `_bake()` /
`proxy_vm_provision` helpers. There is:

- **no operator button** to bake either image — you run a test function;
- **no DocType** that records *"this snapshot was baked from this recipe at this
  commit on this date"* — the provenance is a Task row and tribal knowledge;
- **no shared code** — the duplicated `build_*` will drift;
- **no place** for a third image type (a future "worker image", a custom
  per-customer image) to land without copy-pasting a third `build_*`.

### What this abstraction is *not*

It does **not** replace the committed `bench/` and `proxy/` trees or their
`build.sh` scripts — those stay the source of truth for *what gets installed*
(spec taste #15: scripts are the source of truth for server-side logic). The
abstraction owns the **controller-side lifecycle**: provision, upload, run,
snapshot, register, audit. The recipe just *names* an existing committed tree.

---

## 2. Shape of the solution

Three pieces, smallest surface that removes the duplication and gives the
operator a button:

1. **An `Image Recipe` registry** — *code-defined* (a Python registry, not a
   DocType), one entry per buildable image. Declares the committed source tree,
   the build entrypoint, the build-VM sizing, the post-build finalize hook, and
   what to do with the resulting snapshot. New image type = one small,
   reviewable code change beside `bench/` and `proxy/` — matching the spec's
   "few dependencies / don't import — copy" taste and "versions are pinned,
   bumping is a deliberate code change" discipline the two `build.sh` files
   already follow.

2. **An `Image Build` DocType** — *the operator-facing object*. One row per bake
   run. Owns the **full lifecycle** (provision build VM → upload → build → stop →
   snapshot → optionally register + teardown) as a background job, with a live
   status checklist (the `Site` / `/site-status` pattern). The snapshot it
   produces is its output; the build VM is scratch.

3. **One shared builder seam** — `atlas.atlas.image_builder.run_build(vm,
   recipe)` — the de-duplicated core of `build_bench` + `build_proxy` (upload
   tree, run detached, record Task, finalize). `bench_image.build_bench` and
   `proxy.build_proxy` become thin wrappers that call it with their recipe (so
   the e2e modules and any existing callers keep working unchanged), and the new
   `Image Build` controller calls the same seam.

```
        Image Recipe registry (code)            Image Build (DocType, operator)
        ────────────────────────────            ───────────────────────────────
        bench  → bench/  build.sh  …             one row per bake run
        proxy  → proxy/  build.sh  …             status: Draft→Provisioning→
        (worker → …  later)                        Building→Snapshotting→
                  │                                 Available / Failed
                  │  recipe lookup                  │
                  ▼                                 ▼  after_insert → enqueue
        atlas.atlas.image_builder.run_build(vm, recipe)  ◄── shared seam
        upload tree · run_detached(build.sh) · Task row · finalize hook
                  │
                  ▼
        Virtual Machine Snapshot  ──►  Atlas Settings.default_bench_snapshot
        (the rollable artifact)        / proxy fleet clone source
```

---

## 3. The Image Recipe registry (code-defined)

A frozen dataclass registry in `atlas/atlas/image_recipes.py`, keyed by a short
recipe name. **No DocType** — a recipe points entirely at committed files and
pinned constants, so it *is* code; a data row would only ever mirror it. This is
the same call the spec already makes for sizes (`sizes.py SIZE_PRESETS` is the
"canonical source", mirrored into JS/SPA) and image constants (`DEFAULT_IMAGE`
in `bootstrap.py`).

```python
# atlas/atlas/image_recipes.py
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

@dataclass(frozen=True)
class ImageRecipe:
    name: str                     # "bench" | "proxy" — the registry key
    title: str                    # operator-facing, e.g. "Golden bench image"
    source_directory: str         # repo-relative tree, e.g. "bench" / "proxy"
    build_entrypoint: str         # relative to the staged tree, e.g. "build.sh"
    remote_directory: str         # where the tree is staged in the guest
    base_image: str | None        # Virtual Machine Image name; None = Atlas Settings default
    disk_gigabytes: int           # build-VM disk (bench needs 12; proxy default)
    memory_megabytes: int         # build-VM RAM   (bench needs 2048)
    vcpus: int
    snapshot_title: str           # title stamped on the produced snapshot
    exclude: tuple[str, ...] = () # top-level tree entries to skip (proxy: "test")
    finalize: Callable | None = None   # post-build guest step (proxy: write region + start unit)
    registers_as: str | None = None    # "default_bench_snapshot" → auto-wire on success
    is_proxy: bool = False             # the produced VMs are proxies (region-scoped)

RECIPES: dict[str, ImageRecipe] = {
    "bench": ImageRecipe(
        name="bench",
        title="Golden bench image",
        source_directory="bench",
        build_entrypoint="build.sh",
        remote_directory="/tmp/atlas-bench-build",
        base_image=None,
        disk_gigabytes=12,            # GOLDEN_DISK_GB
        memory_megabytes=2048,        # GOLDEN_MEMORY_MB
        vcpus=2,
        snapshot_title="golden-bench",
        registers_as="default_bench_snapshot",
    ),
    "proxy": ImageRecipe(
        name="proxy",
        title="Reverse proxy image",
        source_directory="proxy",
        build_entrypoint="build.sh",
        remote_directory="/tmp/atlas-proxy-build",
        base_image=None,
        disk_gigabytes=10,
        memory_megabytes=1024,
        vcpus=2,
        snapshot_title="proxy-image",
        exclude=("test",),            # the compose harness is dev-only
        finalize=_finalize_proxy,     # write region + restart atlas-proxy.service
        is_proxy=True,
    ),
}

def get_recipe(name: str) -> ImageRecipe:
    if name not in RECIPES:
        frappe.throw(f"Unknown image recipe {name!r}; known: {sorted(RECIPES)}")
    return RECIPES[name]
```

**Why the two knobs that look like data are still code:**

- `finalize` is a callback — the proxy's post-build step (write `REGION_FILE`,
  `systemctl restart atlas-proxy.service`) is genuinely code and can't be a
  string. The bench recipe has `finalize=None`.
- `registers_as` lets a successful bench bake auto-set
  `Atlas Settings.default_bench_snapshot` (the spec already reads this; today an
  operator wires it by hand). Proxy snapshots aren't a Single field — they feed a
  fleet — so `registers_as=None` and the operator clones proxies from the
  snapshot the build produced.

The recipe **subsumes the per-module constants** that exist today:
`REMOTE_BENCH_DIRECTORY`, `GOLDEN_DISK_GB`, `GOLDEN_MEMORY_MB`,
`REMOTE_PROXY_DIRECTORY`, the `test/` exclude, the proxy finalize block. They all
move into the two recipe entries; the modules stop carrying them.

---

## 4. DocTypes

### 4.1 `Image Build` (new)

The operator-facing object: one row per bake run. **Naming:** autoname a
hash/`IMG-BUILD-.#####` series — a recipe can be re-baked many times, so the name
isn't the recipe. Submittable? **No** — it's a job-runner row like a Task, not a
ledger entry; status carries the state.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `recipe` | Select (options from `RECIPES` keys: `bench`, `proxy`) | which image to bake. `set_only_once`. |
| `title` | Data | denormalized from the recipe for the list view; read-only. |
| `server` | Link → Server | where the build VM is provisioned. Required; the operator picks an `Active` server (no scheduler — spec principle #4). For `proxy`, this also fixes the region via the server. |
| `region` | Data | proxy recipes only; derived/required when `is_proxy`. Drives the finalize hook + the produced VM's `region`. |
| `base_image` | Link → Virtual Machine Image | the stock Ubuntu base to build on. Defaults from the recipe / Atlas Settings. `set_only_once`. |
| `status` | Select | `Draft` → `Provisioning` → `Building` → `Snapshotting` → `Available` / `Failed`. The one source of truth for the checklist. |
| `build_virtual_machine` | Link → Virtual Machine | the scratch VM this build provisioned. Filled at step 1. |
| `snapshot` | Link → Virtual Machine Snapshot | **the output.** Filled at step 4; this is what the build exists to produce. |
| `build_task` | Link → Task | the `image-build` Task row recording the in-guest `build.sh` run (stdout/stderr/exit). The audit trail. |
| `auto_register` | Check | if set and the recipe has `registers_as`, wire the snapshot into Atlas Settings on success. Default on for `bench`. |
| `terminate_build_vm` | Check | if set, terminate the scratch build VM after a successful snapshot (the snapshot is durable and outlives it — spec/14 "durable artifact that outlives its build VM"). Default **off** (today's behavior: leave it Stopped for re-bake/inspection). |
| `error` | Small Text | the failure tail on `Failed` (last 500 chars of stderr), read-only. |

**Permissions.** System Manager (operator) only — `Image Build`, like `Provider`
/ `Server` / `Task`, is **invisible and access-denied to the SPA `Atlas User`**
(it is not in `_OWNED_DOCTYPES`; the SPA never lists it). Baking images is an
operator-fleet operation, not a per-user one.

**Lifecycle (controller):**

1. `validate` — resolve the recipe; copy `title`; for an `is_proxy` recipe
   require `region` (and that the chosen server is in it); default `base_image`
   from recipe/Atlas Settings; guard `recipe`/`server`/`base_image` immutable
   after insert (`set_only_once` + a `_validate_immutability` like
   `Virtual Machine`).
2. `after_insert` — `frappe.enqueue(self.run, queue="long")` (it SSHes and waits
   ~10–20 min — the same `queue="long"` the `Site.auto_provision` and image-sync
   jobs use). No-op if not `Draft`.
3. `run()` — the orchestration, below.
4. `terminate_failed()` / re-bake — an operator verb to clean up a `Failed`
   build's scratch VM, and a **Re-bake** button that re-runs `run()` (idempotent:
   `build.sh` is idempotent by spec taste #16, so re-running a stuck build is
   safe).

### 4.2 `Virtual Machine Snapshot` (existing — unchanged shape, new producer)

The output type already exists and already carries everything a clone needs
(`source_image`, `disk_gigabytes`, `server`, `rootfs_path`). The Image Build is
just a **new, audited producer** of these rows. One optional addition for
provenance (nice-to-have, not required):

| Field (optional add) | Type | Notes |
| -------------------- | ---- | ----- |
| `image_build` | Link → Image Build | back-pointer to the bake that produced this snapshot. Lets an operator answer "what recipe/commit is this golden snapshot?" from the snapshot itself. |

If we add it, set it in `run()` step 4. If we keep the snapshot DocType frozen,
the `Image Build.snapshot` forward link alone carries the provenance.

### 4.3 `Atlas Settings` (existing — unchanged)

Already has `default_bench_snapshot`. The build's `auto_register` +
`registers_as="default_bench_snapshot"` writes it on success, replacing the
manual wiring step that exists today. No schema change.

---

## 5. The shared builder seam

`atlas/atlas/image_builder.py` — the de-duplicated core. The two existing
`build_*` functions collapse into one:

```python
# atlas/atlas/image_builder.py
def run_build(virtual_machine: str, recipe: ImageRecipe) -> None:
    """Upload the recipe's committed tree into the guest and run its build.sh
    DETACHED, then run the recipe's finalize hook. Records one `image-build`
    Task. The de-duplicated core of the old build_bench / build_proxy."""
    vm = frappe.get_doc("Virtual Machine", virtual_machine)
    connection = connection_for_guest(vm)
    uploads = _tree_uploads(recipe)            # rglob, exclude, __pycache__ filter
    forget_host(connection.host)               # recycled-IP host-key trap
    with ssh_key_file(connection.ssh_private_key) as key_path:
        _stage_tree(connection, key_path, uploads)         # mkdir -p + scp loop
        stdout, stderr, code = run_detached(               # the long build
            connection, key_path,
            f"chmod +x {entrypoint} && {entrypoint}",
            log_path=..., done_path=...,
        )
        if code == 0 and recipe.finalize:
            stdout, stderr, code = recipe.finalize(vm, connection, key_path)
    _record_guest_task(virtual_machine, "image-build", {"recipe": recipe.name},
                       stdout, stderr, code)
    if code != 0:
        frappe.throw(f"{recipe.title} build on {virtual_machine} failed "
                     f"(exit {code}): {stderr[-500:]}")
```

`_tree_uploads`, `_stage_tree`, `_remote_parent`, the `__pycache__` filter, the
`_record_guest_task` shape — all lifted verbatim from the two modules (they're
already identical). The `forget_host` + `run_detached` + throw-on-nonzero
discipline is preserved exactly (it encodes hard-won host facts:
real-provision-traps #1, the SSH-reset-mid-build fragility).

**Backwards-compatible wrappers** (so e2e + any caller keeps working):

```python
# atlas/atlas/bench_image.py  (now ~5 lines)
def build_bench(virtual_machine: str) -> None:
    run_build(virtual_machine, get_recipe("bench"))

# atlas/atlas/proxy.py  (build_proxy now ~5 lines; reconcile/push_cert untouched)
def build_proxy(virtual_machine: str) -> None:
    vm = frappe.get_doc("Virtual Machine", virtual_machine)
    _assert_proxy(vm)                         # keep the is_proxy/region guards
    run_build(virtual_machine, get_recipe("proxy"))
```

The proxy's finalize (write `REGION_FILE`, restart `atlas-proxy.service`) moves
into `_finalize_proxy(vm, connection, key_path)` in `image_recipes.py`,
referenced by the recipe. `proxy.py` keeps `reconcile_*`, `push_cert`,
`canonical_json`, `wildcard_targets_for_region`, `_record_guest_task` — only the
upload/build half of `build_proxy` moves.

### 5.1 The orchestration — `Image Build.run()`

This is the part that exists **only in e2e helpers today** (`_bake`,
`proxy_vm_provision`) and becomes first-class:

```python
def run(self):
    if self.status != "Draft":
        return                                  # idempotent; already running/done
    recipe = get_recipe(self.recipe)
    try:
        self._provision_build_vm(recipe)        # status → Provisioning
        run_build(self.build_virtual_machine, recipe)   # status → Building
        self._snapshot(recipe)                  # status → Snapshotting → Available
        if self.auto_register and recipe.registers_as:
            self._register(recipe)              # wire Atlas Settings
        if self.terminate_build_vm:
            self._terminate_build_vm()
    except Exception:
        self.db_set("status", "Failed")
        self.db_set("error", frappe.get_traceback()[-500:])
        raise                                   # fail loud — job log carries it
```

- `_provision_build_vm` — insert a `Virtual Machine` at the recipe's
  size/disk/memory on `self.server` from `self.base_image`, `wait_for_vm_running`.
  (Today's `_provision_build_vm` in `bench_image.py`, minus the dual-key e2e
  hack — production uses the baked Atlas key like every VM.) `is_proxy`/`region`
  set from the recipe.
- `_snapshot` — `vm.stop()`, assert `Stopped`, `snapshot(title=recipe.snapshot_title)`,
  link it into `self.snapshot`, status `Available`. (Today's `_bake` steps 3.)
- `_register` — `Atlas Settings.default_bench_snapshot = self.snapshot` (+ commit).
- realtime — `publish_realtime("image_build", {...})` on each transition, so a
  desk **live checklist** updates without reload (the `Site.auto_provision`
  pattern, §4 of self-serve).

---

## 6. Wireframes

### 6.1 Desk — Image Build list (`/app/image-build`)

```
┌─ Image Build ───────────────────────────────────────── [ + New Build ] ─┐
│                                                                          │
│  Name            Recipe   Region    Status        Snapshot       Created │
│  ──────────────  ───────  ────────  ────────────  ─────────────  ─────── │
│  IMG-BUILD-00007 bench    —         ● Available    golden-bench…  2h ago  │
│  IMG-BUILD-00006 proxy    blr1      ● Available    proxy-image-…  1d ago  │
│  IMG-BUILD-00005 bench    —         ✖ Failed       —              2d ago  │
│  IMG-BUILD-00004 proxy    sgp1      ◐ Building      —              just now│
│                                                                          │
└──────────────────────────────────────────────────────────────────────── ┘
   Status dot: ● Available (green) · ◐ Provisioning/Building/Snapshotting
   (blue, animated) · ✖ Failed (red)
```

### 6.2 Desk — New Build dialog (the `+ New Build` form)

```
┌─ New Image Build ────────────────────────────────────────────────┐
│                                                                   │
│  Recipe        [ bench ▾ ]   ← Golden bench image                 │
│                  bench  · Golden bench image                      │
│                  proxy  · Reverse proxy image                     │
│                                                                   │
│  Server        [ blr1-host-3        ▾ ]  (Active servers only)    │
│  Region        [ —                    ]  (shown only for proxy)   │
│  Base image    [ ubuntu-24.04-server  ▾ ]  (defaults from recipe) │
│                                                                   │
│  ☑ Auto-register as default_bench_snapshot   (bench only)         │
│  ☐ Terminate build VM after snapshot                              │
│                                                                   │
│  ⓘ Provisions a scratch VM, runs build.sh inside it (~10–20 min), │
│    then snapshots it. The snapshot is the output; the build VM    │
│    is scratch.                                                    │
│                                                    [ Cancel ] [ Bake ] │
└───────────────────────────────────────────────────────────────────┘
```

`Recipe` is a Select sourced from `RECIPES`. Selecting `proxy` reveals
`Region` (required) and hides `Auto-register`. `Bake` saves the row →
`after_insert` enqueues the job → redirect to the form (6.3).

### 6.3 Desk — Image Build form, live checklist (the running view)

The `Site` `/site-status` pattern applied to a desk form: a live status intro
derived from `status`, pushed over realtime with a polling fallback.

```
┌─ IMG-BUILD-00008 · Golden bench image ───────────── ● Building ──┐
│                                                                  │
│  Baking golden bench image on blr1-host-3                        │
│  ───────────────────────────────────────────                    │
│   ✔  Provision build VM        vm-7f3a (Running)                 │
│   ⟳  Build inside guest        running build.sh … (bench init)   │
│   ·   Stop + snapshot          pending                           │
│   ·   Register / finalize      pending                           │
│                                                                  │
│  Build VM     vm-7f3a   v6 2604:…:a1                             │
│  Base image   ubuntu-24.04-server                                │
│  Build Task   TASK-00451  (live stdout in the Task row)          │
│                                                                  │
│              [ Re-bake ]   [ View Build Task ]   [ Terminate VM ]│
└──────────────────────────────────────────────────────────────────┘
```

On `Available`:

```
┌─ IMG-BUILD-00008 · Golden bench image ──────────── ● Available ──┐
│                                                                  │
│   ✔  Provision build VM    ✔ Build    ✔ Snapshot    ✔ Registered │
│                                                                  │
│  Snapshot     golden-bench-2026-06-11   (Available)              │
│               → set as Atlas Settings.default_bench_snapshot ✔   │
│  Build VM     vm-7f3a   (Stopped — kept for re-bake)             │
│                                                                  │
│  Site VMs now clone this snapshot via                            │
│  Virtual Machine Snapshot.clone_to_new_vm.                       │
│                                                                  │
│              [ Re-bake ]   [ Terminate build VM ]   [ Open snapshot ]│
└──────────────────────────────────────────────────────────────────┘
```

On `Failed`:

```
│   ✔ Provision   ✖ Build (exit 1)   · Snapshot   · Register        │
│                                                                   │
│   error: bench init failed: E: Unable to locate package …         │
│          (last 500 chars; full log in Build Task TASK-00451)      │
│                                                                   │
│              [ Re-bake ]   [ Terminate build VM ]   [ View Task ]  │
```

### 6.4 Server form — Bake Image action (entry point parity with Sync Image)

The image-sync flow already adds a **Sync Image** item to the Server form's
`Actions ▾`. Bake gets the same treatment so the operator can start a bake from
the server they're looking at:

```
   Server: blr1-host-3        [ Actions ▾ ]
                               ├ Run Task
                               ├ Reboot
                               ├ Sync Image
                               └ Bake Image …        ← opens 6.2 prefilled server
```

---

## 7. Operator use-case integration

Add **one row** to the spec README's operator use-case table (the index the e2e
suite mirrors):

| Use case | Operator action | Spec |
| -------- | --------------- | ---- |
| Bake an image | `Image Build` → **New / Bake**, or `Server` → **Bake Image** | `spec/15-image-builder.md` |

The e2e modules `bench_image.py` and `proxy_vm.py` **don't go away** — they keep
proving the host facts the bake exists to prove (`bench --version` over guest-SSH;
the proxy compiles + serves). They are **re-pointed** to drive the new operator
path: instead of calling the bare `build_bench` / `build_proxy` and hand-rolling
provision+snapshot, they insert an `Image Build` row and assert it reaches
`Available` — so the e2e now covers the operator button, not just the seam. The
`run_build` seam keeps its own thin unit coverage (tree enumeration, exclude
filter, Task-record, fail-loud) in `test_image_builder.py`, lifted from the
overlapping bits of `test_bench_image.py` + `test_proxy.py`.

The **self-serve** e2e (`self_serve_site.py`) already bakes a golden snapshot
inline when `Atlas Settings.default_bench_snapshot` is unset; it switches to
"insert an `Image Build(recipe=bench, auto_register=1)` and wait for Available",
which both bakes *and* wires the setting — removing the bespoke inline-bake
branch.

---

## 8. What moves where (migration map)

| Today | After |
| ----- | ----- |
| `bench_image.build_bench` (full upload+build body) | thin wrapper → `run_build(vm, get_recipe("bench"))` |
| `proxy.build_proxy` (upload+build body) | thin wrapper → `run_build`; guards stay; finalize → `_finalize_proxy` |
| `REMOTE_BENCH_DIRECTORY`, `GOLDEN_DISK_GB`, `GOLDEN_MEMORY_MB` | `image_recipes.RECIPES["bench"]` |
| `REMOTE_PROXY_DIRECTORY`, `test/` exclude, proxy finalize block | `image_recipes.RECIPES["proxy"]` + `_finalize_proxy` |
| `_tree_uploads`, `_remote_parent`, `_stage_tree` (dup in 2 files) | `image_builder.py` (one copy) |
| e2e `_bake()` / `proxy_vm_provision` (provision→stop→snapshot orchestration) | `Image Build.run()` controller |
| manual `Atlas Settings.default_bench_snapshot = …` | `auto_register` + `registers_as` |
| Task `script="bench-build"` / `"proxy-build"` | Task `script="image-build"`, `variables={"recipe": …}` (or keep per-recipe names — see open question) |

**Net:** `bench_image.py` and `proxy.py` both shrink; the duplicated build core
lives once; the orchestration becomes a DocType the operator can see, click, and
audit; a third image type is a recipe entry + a committed tree, no new module.

---

## 9. How the build resolved the open questions

1. **Task `script` name** — kept **distinct** `bench-build` / `proxy-build` names
   via `recipe.task_script`, so the operator's Task list stays readable. (Not the
   single `image-build` name sketched in §5/§8.)
2. **Snapshot back-link** — **not added.** Provenance rides the
   `Image Build.snapshot` forward link only; `Virtual Machine Snapshot` stays
   frozen. The back-link is a cheap future add if "what is this snapshot?" from the
   snapshot side becomes a real need.
3. **Region for proxy builds** — asked **explicitly** in the dialog (the `Image
   Build.region` field), required for an `is_proxy` recipe, rather than derived
   from the server. Simpler than threading server→region and lets a build target a
   region label directly.
4. **Auto-terminate default** — `terminate_build_vm` ships **off**: the build VM
   is left Stopped for re-bake / inspection. Scratch ≠ auto-deleted; the snapshot
   is the durable artifact (spec/14).
5. **Concurrency** — no hard lock. A second `Image Build` on a busy server just
   provisions another VM. Two bakes of the same recipe racing to `auto_register`
   the same Single field is last-writer-wins (acceptable).

One planned step **not shipped**: re-pointing the `bench_image` / `proxy_vm` e2e
modules (and `self_serve_site`'s inline bake) to drive the `Image Build` DocType
instead of the bare build verbs (§7). The verbs keep their public signatures, so
those modules work unchanged through the new seam; the DocType-driven e2e is a
documented, host-verifiable follow-up.
```
