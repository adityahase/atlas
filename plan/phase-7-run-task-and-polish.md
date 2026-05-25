# Phase 7 — Run-Task escape hatch + Task list polish

## Goal

Land the "Run Task" dialog on Server (the escape hatch from
[`../spec/04-tasks.md#run-task--the-escape-hatch`](../spec/04-tasks.md#run-task--the-escape-hatch)),
the Reboot button, and the list-view/dashboard polish that makes the operator
experience usable.

This phase is small in code, large in operator value.

## You can do this at the end

- On a Server, click **Run Task** → dialog with a script picker (Select
  field populated from
  [`../scripts/`](../scripts/)) and a JSON variables field. Submit → a Task
  row is created and run, same code path as everything else.
- On a Server, click **Reboot** → `systemctl reboot` runs as a Task. (One
  Task with a `reboot-server.sh` script we add this phase.)
- Open the Task list view → columns: server, virtual_machine, script,
  status, duration. Sort by duration desc shows slowest scripts first.
- Open a Server form → "Virtual Machines on this server" child-table view
  shows VMs with status, IPv6, vCPUs, RAM. "Recent Tasks" view shows the
  last 10 Tasks for the server.

## Files added or changed

### DocTypes

- `atlas/atlas/atlas/doctype/server/server.py` — add `run_task_dialog(self,
  script: str, variables: dict)` whitelisted method.
- `atlas/atlas/atlas/doctype/server/server.js` — implement the Run Task
  dialog, swap Reboot from stub to real, add the two child-table-style
  HTML areas.

### Scripts (new)

- `scripts/reboot-server.sh` — one-liner: `sudo systemctl reboot`. We make
  it a real script so the path through `run_task` is uniform.

### Module additions

- `atlas/atlas/atlas/scripts_catalog.py` — enumerates the scripts under
  `scripts/`. Used by the Run Task dialog and by `execute_task` (path
  resolution). One source of truth for "what scripts exist."

### Tests

- `atlas/atlas/atlas/doctype/server/test_server_runtask.py`
- `atlas/atlas/tests/e2e/phase_7.py`

## Run Task implementation

Server-side:

```python
@frappe.whitelist()
def run_task_dialog(self, script: str, variables: dict | str | None = None) -> str:
    """Same code path as bootstrap/provision. Returns Task name.

    `variables` may be a dict (from JS) or a JSON string (from CLI someday)."""
    if isinstance(variables, str):
        variables = json.loads(variables or "{}")
    if script not in scripts_catalog.allowed_scripts():
        frappe.throw(f"Unknown script: {script}")
    task = run_task_on_server(
        server=self.name,
        script=script,
        variables=variables or {},
        timeout_seconds=1800,
    )
    return task.name
```

`scripts_catalog.allowed_scripts()` returns the list of `.sh` files under
`scripts/` (not `scripts/guest/` — those are guest unit files, not
runnable on the host).

### JS dialog

```javascript
frm.add_custom_button('Run Task', () => {
    const dialog = new frappe.ui.Dialog({
        title: 'Run Task',
        fields: [
            {
                fieldname: 'script', label: 'Script', fieldtype: 'Select',
                options: frm.doc.__atlas_scripts.join('\n'), reqd: 1,
            },
            {
                fieldname: 'variables', label: 'Variables (JSON)',
                fieldtype: 'Code', options: 'JSON', default: '{}',
            },
        ],
        primary_action_label: 'Run',
        primary_action(values) {
            frm.call('run_task_dialog', {
                script: values.script,
                variables: values.variables,
            }).then(({message: task_name}) => {
                dialog.hide();
                frappe.set_route('Form', 'Task', task_name);
            });
        },
    });
    dialog.show();
});
```

The script list is loaded into `frm.doc.__atlas_scripts` by a `refresh()`
call to a whitelisted `get_scripts()` method (so we don't hard-code in JS).

## Reboot

```python
@frappe.whitelist()
def reboot(self) -> str:
    return self.run_task_dialog(script="reboot-server.sh", variables={})
```

The reboot Task will end in a Failure or in a "broken pipe" because SSH drops
when the server reboots. `run_task` treats SSH connection drops as
Failure with stderr capture; that's acceptable. The operator confirms reboot
by waiting and checking. We do **not** auto-poll the server.

## Server form polish

Two HTML areas, populated from `Server.get_form_extras(self) -> dict`:

```python
def get_form_extras(self) -> dict:
    return {
        "virtual_machines": frappe.get_all(
            "Virtual Machine",
            filters={"server": self.name},
            fields=["name", "description", "status", "vcpus",
                    "memory_megabytes", "ipv6_address"],
            order_by="creation desc",
            limit=50,
        ),
        "recent_tasks": frappe.get_all(
            "Task",
            filters={"server": self.name},
            fields=["name", "script", "status", "duration_milliseconds",
                    "creation"],
            order_by="creation desc",
            limit=10,
        ),
    }
```

JS renders them as simple HTML tables. No fancy components. Espresso /
Frappe UI optional — for the iteration, native `frappe.render_template`
with a Jinja template is plenty.

## Task list-view polish

In `task.json`:

- `title_field`: `"name"`.
- `name_case`: leave as is.
- `image_field`: empty.
- `quick_entry`: 0.
- `list_view_settings.fields`: ensure `server`, `virtual_machine`, `script`,
  `status`, `duration_milliseconds`, `creation` are in
  `list_view_settings` (or `in_list_view = 1`).
- Index `duration_milliseconds` so sort-by-slowest is cheap.

In `task.js`:

```javascript
frappe.listview_settings['Task'] = {
    add_fields: ['status', 'duration_milliseconds'],
    get_indicator(doc) {
        return {
            Pending:  ['Pending',  'orange', 'status,=,Pending'],
            Running:  ['Running',  'blue',   'status,=,Running'],
            Success:  ['Success',  'green',  'status,=,Success'],
            Failure:  ['Failure',  'red',    'status,=,Failure'],
        }[doc.status];
    },
};
```

## Test plan

### Unit tests

- `test_run_task_dialog_rejects_unknown_script`: pass `"rm-rf-everything.sh"`,
  assert raise.
- `test_run_task_dialog_calls_run_task_on_server`: mock, assert called with
  right args.
- `test_run_task_dialog_parses_string_variables_as_json`.
- `test_allowed_scripts_lists_real_files`: assert
  `scripts_catalog.allowed_scripts()` is non-empty and includes
  `bootstrap-server.sh`.
- `test_get_form_extras_returns_lists`.

### E2E (`tests/e2e/phase_7.py`)

Builds on phase 6. Reuses a bootstrapped server.

1. Pre-sweep.
2. Provision/reuse server.
3. Call `server.run_task_dialog(script="bootstrap-server.sh", variables={
       "FIRECRACKER_VERSION": "v1.15.1", "ARCHITECTURE": "x86_64"})`.
4. Block on Task. Assert Success (re-bootstrap is idempotent).
5. Call `server.run_task_dialog(script="nope.sh", variables={})`. Assert
   raise.
6. **Reboot**: call `server.reboot()`. Assert a Task exists; its status
   may be Failure (because SSH drops). Sleep 60s. Probe-Task `uname -r`
   succeeds (server back up).
7. `finally`: leave the server.

## What we are NOT doing in this phase

- No streaming logs in the dialog. Operator opens the resulting Task to
  read stdout when done.
- No script-argument autocomplete or schema. JSON in, JSON validated, that's
  it.
- No history of dialog usages.
- No keyboard shortcuts.
- No fancy form widgets. Plain HTML tables for the two extras.
- No `Run Task` button on Virtual Machine (only on Server). Operator can
  navigate from the Server.
- No "Resume from failed Task" button. To replay: open the Task, copy
  `script` and `variables` into a new Run Task dialog.

## Spec drift introduced

See [drift.md](./drift.md#phase-7):

- `Run Task` dialog passes the variable JSON as a dict to the whitelisted
  method (Frappe deserializes JSON request bodies). The spec didn't say.
- `scripts_catalog.allowed_scripts()` whitelists the directory listing.
  Spec implied the dialog is a "picker over the scripts directory" — we
  pin the source to `scripts/*.sh` excluding `guest/` and `systemd/`.
- We add a `reboot-server.sh` script (one line) so the reboot path goes
  through `run_task`. Spec said "Reboot — systemctl reboot over SSH";
  this is the natural implementation.
