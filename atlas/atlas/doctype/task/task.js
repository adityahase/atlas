const HEADLINE_BY_STATUS = {
	Pending: {color: "blue", text: "Queued — waiting for worker."},
	Running: {color: "yellow", text: "Running"},
	Success: {color: "green", text: "Completed"},
	Failure: {color: "red", text: "Failed"},
};


frappe.ui.form.on("Task", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.disable_save();
		render_headline(frm);
		render_chips(frm);
		add_retry_button(frm);
		render_sibling_tasks(frm);
		pretty_print_variables(frm);
		subscribe_to_realtime(frm);
	},
	onload(frm) {
		// One global subscription per form lifecycle. Realtime updates fire
		// before refresh in some cases, so we register early.
		frm._atlas_realtime_registered = false;
	},
});


function render_headline(frm) {
	const status = frm.doc.status;
	const config = HEADLINE_BY_STATUS[status];
	if (!config) return;

	let text = config.text;
	const duration = describe_duration(frm.doc.duration_milliseconds);
	if (status === "Running") {
		const started = frappe.datetime.comment_when(frm.doc.started);
		text = `${config.text} on ${frappe.utils.escape_html(frm.doc.server || "—")} — started ${started}.`;
	} else if (status === "Success") {
		text = `Completed in ${duration}. Exit code ${frm.doc.exit_code ?? 0}.`;
	} else if (status === "Failure") {
		const first_line = first_stderr_line(frm.doc.stderr);
		text = `Failed in ${duration}. Exit code ${frm.doc.exit_code ?? "—"}.`;
		if (first_line) {
			text += `<br><span class="text-muted small">${frappe.utils.escape_html(first_line)}</span>`;
		}
	}
	frm.dashboard.clear_headline();
	frm.dashboard.set_headline_alert(text, config.color);
}


function describe_duration(milliseconds) {
	if (!milliseconds) return "—";
	const seconds = Math.round(milliseconds / 1000);
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.floor(seconds / 60);
	const remainder = seconds % 60;
	return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}


function first_stderr_line(stderr) {
	if (!stderr) return "";
	return stderr
		.split("\n")
		.map((l) => l.trim())
		.filter((l) => l && !l.startsWith("+ "))
		.slice(0, 1)[0] || "";
}


function render_chips(frm) {
	frm.dashboard.clear_headline_indicators?.();
	const dashboard = frm.dashboard;
	if (!dashboard || !dashboard.add_indicator) return;
	if (frm.doc.server) {
		const href = `/app/server/${encodeURIComponent(frm.doc.server)}`;
		dashboard.add_indicator(
			`Server: <a href="${href}">${frappe.utils.escape_html(frm.doc.server)}</a>`,
			"blue",
		);
	}
	if (frm.doc.virtual_machine) {
		frappe.db.get_value("Virtual Machine", frm.doc.virtual_machine, "description")
			.then(({message}) => {
				const description = message?.description || frm.doc.virtual_machine.slice(0, 8);
				const href = `/app/virtual-machine/${encodeURIComponent(frm.doc.virtual_machine)}`;
				dashboard.add_indicator(
					`VM: <a href="${href}">${frappe.utils.escape_html(description)}</a>`,
					"blue",
				);
			});
	}
	if (frm.doc.triggered_by) {
		dashboard.add_indicator(
			`Triggered by ${frappe.utils.escape_html(frm.doc.triggered_by)}`,
			"grey",
		);
	}
}


function add_retry_button(frm) {
	if (frm.doc.status !== "Failure") return;
	frappe.atlas.add_primary(frm, "Retry", () => {
		frappe.confirm(__("Retry this Task?"), () => {
			frm.call("retry").then(({message: task_name}) => {
				frappe.atlas.task_started(frm, "Retry", task_name);
			});
		});
	});
}


function render_sibling_tasks(frm) {
	const filter_field = frm.doc.virtual_machine
		? "virtual_machine"
		: frm.doc.server
			? "server"
			: null;
	if (!filter_field) return;
	const wrapper_id = "atlas-sibling-tasks";
	frm.dashboard.wrapper?.find(`#${wrapper_id}`).remove();
	const $section = $(`<div id="${wrapper_id}"></div>`);
	frm.dashboard.add_section($section, __("Sibling Tasks"));
	frappe.widget.make_widget({
		widget_type: "quick_list",
		document_type: "Task",
		label: __("Sibling Tasks"),
		quick_list_filter: JSON.stringify([
			[filter_field, "=", frm.doc[filter_field]],
			["name", "!=", frm.doc.name],
		]),
		container: $section,
		options: {},
	});
}


function pretty_print_variables(frm) {
	const raw = frm.doc.variables;
	if (!raw || frm._atlas_variables_prettified === frm.doc.name) return;
	let parsed;
	try {
		parsed = JSON.parse(raw);
	} catch (e) {
		return;
	}
	const pretty = JSON.stringify(parsed, null, 2);
	if (pretty === raw) {
		frm._atlas_variables_prettified = frm.doc.name;
		return;
	}
	frm.doc.variables = pretty;
	frm.refresh_field("variables");
	frm._atlas_variables_prettified = frm.doc.name;
}


function subscribe_to_realtime(frm) {
	if (frm._atlas_realtime_registered) return;
	frm._atlas_realtime_registered = true;
	frappe.realtime.on("task_update", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		frm.reload_doc();
	});
}
