frappe.ui.form.on("Server", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		add_buttons(frm);
		render_running_task_headline(frm);
		render_recent_tasks(frm);
		subscribe_to_realtime(frm);
	},
});


function render_running_task_headline(frm) {
	frm.dashboard.clear_headline?.();
	frappe.db.get_list("Task", {
		fields: ["name", "subject", "script", "status", "started", "modified"],
		filters: {
			server: frm.doc.name,
			status: ["in", ["Pending", "Running"]],
		},
		order_by: "modified desc",
		limit: 1,
	}).then((rows) => {
		if (!rows.length) return;
		const task = rows[0];
		const subject = task.subject || task.script || task.name;
		const when_started_html = task.started
			? frappe.datetime.comment_when(task.started)
			: `<span class="text-muted small">${__("just now")}</span>`;
		const link = `<a href="/app/task/${encodeURIComponent(task.name)}">${frappe.utils.escape_html(subject)} →</a>`;
		frm.dashboard.set_headline_alert(
			`⏵ ${__("Running task")}: ${link} <span class="text-muted small">${when_started_html}</span>`,
			"yellow",
		);
	});
}


function render_recent_tasks(frm) {
	const wrapper_id = "atlas-server-recent-tasks";
	frm.dashboard.wrapper?.find(`#${wrapper_id}`).remove();
	const $section = $(`<div id="${wrapper_id}"></div>`);
	frm.dashboard.add_section($section, __("Recent Tasks"));
	frappe.widget.make_widget({
		widget_type: "quick_list",
		document_type: "Task",
		label: __("Recent Tasks"),
		quick_list_filter: JSON.stringify([["server", "=", frm.doc.name]]),
		container: $section,
		options: {},
	});
}


function subscribe_to_realtime(frm) {
	if (frm._atlas_server_realtime_registered) return;
	frm._atlas_server_realtime_registered = true;
	frappe.realtime.on("task_update", (data) => {
		if (!data || data.server !== frm.doc.name) return;
		render_running_task_headline(frm);
		render_recent_tasks(frm);
	});
}


function add_buttons(frm) {
	const status = frm.doc.status;
	if (["Pending", "Bootstrapping", "Broken"].includes(status)) {
		frappe.atlas.add_primary(frm, "Bootstrap", () => confirm_bootstrap(frm));
	} else {
		frappe.atlas.add_action(frm, "Re-bootstrap", () => confirm_bootstrap(frm));
	}
	frappe.atlas.add_action(frm, "Run Task", () => open_run_task_dialog(frm));
	frappe.atlas.add_danger(frm, "Reboot", () => confirm_reboot(frm));
}


function confirm_bootstrap(frm) {
	frappe.confirm(__("Bootstrap {0}?", [frm.doc.name]), () => {
		frm.call("bootstrap").then(({message}) => {
			frappe.atlas.task_started(frm, "Bootstrap", message);
		});
	});
}


function confirm_reboot(frm) {
	frappe.db.count("Virtual Machine", {
		filters: {server: frm.doc.name, status: "Running"},
	}).then((running_count) => {
		const body = `
			<p>${__("This will reboot {0}.", [`<b>${frappe.utils.escape_html(frm.doc.name)}</b>`])}</p>
			<p>${__("Running virtual machines: {0}. All will lose connectivity until the host returns.", [`<b>${running_count}</b>`])}</p>
			<p>${__("SSH will drop mid-Task — the reboot Task may end Status = Failure. That is normal.")}</p>
		`;
		frappe.atlas.confirm_destructive({
			title: __("Reboot {0}?", [frm.doc.name]),
			body_html: body,
			match_string: frm.doc.name,
			match_label: __("Type the server name to confirm"),
			proceed_label: __("Reboot"),
			proceed() {
				frm.call("reboot").then(({message}) => {
					frappe.atlas.task_started(frm, "Reboot", message);
				});
			},
		});
	});
}


function open_run_task_dialog(frm) {
	frm.call("get_scripts").then(({message: scripts}) => {
		const dialog = build_run_task_dialog(frm, scripts);
		dialog.show();
	});
}


function build_run_task_dialog(frm, scripts) {
	const is_system_manager = (frappe.user_roles || []).includes("System Manager");
	const by_name = Object.fromEntries(scripts.map((s) => [s.name, s]));

	const fields = [
		{
			fieldname: "script",
			label: __("Script"),
			fieldtype: "Select",
			options: scripts.map((s) => s.name).join("\n"),
			reqd: 1,
			onchange() {
				refresh_script_intro(dialog, by_name[dialog.get_value("script")]);
				dialog.refresh_dependency?.();
			},
		},
		{fieldname: "script_intro", fieldtype: "HTML"},
	];

	for (const script of scripts) {
		for (const field of script.fields || []) {
			fields.push(dialog_field_for_script(field, script.name));
		}
	}

	if (is_system_manager) {
		fields.push(
			{fieldname: "show_advanced", label: __("Show advanced (System Manager)"), fieldtype: "Check"},
			{
				fieldname: "_advanced_variables",
				label: __("Variables (raw JSON)"),
				fieldtype: "Code",
				options: "JSON",
				depends_on: "eval:doc.show_advanced",
				description: __("Posted verbatim. Use only for debugging."),
				default: "{}",
			},
		);
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Run Task"),
		fields: fields,
		primary_action_label: __("Run"),
		primary_action(values) {
			const script = values.script;
			let variables;
			if (is_system_manager && values.show_advanced) {
				variables = values._advanced_variables || "{}";
			} else {
				variables = collect_typed_variables(by_name[script], values);
			}
			frm.call("run_task_dialog", {script, variables}).then(({message: task_name}) => {
				dialog.hide();
				frappe.atlas.task_started(frm, script, task_name);
			});
		},
	});

	if (scripts.length === 1) {
		dialog.set_value("script", scripts[0].name);
		refresh_script_intro(dialog, scripts[0]);
		dialog.refresh_dependency?.();
	}

	return dialog;
}


function dialog_field_for_script(field, script_name) {
	// Server-supplied field dicts use server vocab (`filters` for Link
	// queries). Translate to the Dialog-control shape and gate the field
	// on the parent script via depends_on.
	const {filters, reqd, ...rest} = field;
	const gate = `eval:doc.script === ${JSON.stringify(script_name)}`;
	const out = {
		...rest,
		depends_on: gate,
		mandatory_depends_on: reqd ? gate : undefined,
		reqd: 0,
	};
	if (filters) {
		out.get_query = () => ({filters});
	}
	return out;
}


function refresh_script_intro(dialog, script) {
	const field = dialog.fields_dict.script_intro;
	if (!field || !field.$wrapper) return;
	const intro = script && script.intro;
	if (!intro) {
		field.$wrapper.empty();
		return;
	}
	field.$wrapper.html(
		`<div class="text-muted small">ⓘ ${frappe.utils.escape_html(intro)}</div>`,
	);
}


function collect_typed_variables(script, values) {
	if (!script || !script.fields) return {};
	const variables = {};
	for (const field of script.fields) {
		const value = values[field.fieldname];
		if (value !== undefined && value !== null && value !== "") {
			variables[field.fieldname] = value;
		}
	}
	return variables;
}
