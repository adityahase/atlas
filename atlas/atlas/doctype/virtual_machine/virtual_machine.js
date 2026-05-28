const PRIMARY_BY_STATUS = {
	Pending: {label: "Provision", method: "provision"},
	Failed: {label: "Provision", method: "provision"},
	Stopped: {label: "Start", method: "start"},
	Running: {label: "Stop", method: "stop"},
};

const SECONDARY_BY_STATUS = {
	Running: [{label: "Restart", method: "restart"}],
	Stopped: [{label: "Restart", method: "restart"}],
};


const SIZE_PRESETS = {
	"Small (1 vCPU / 512 MB / 4 GB)": {vcpus: 1, memory_megabytes: 512, disk_gigabytes: 4},
	"Medium (2 vCPU / 2048 MB / 10 GB)": {vcpus: 2, memory_megabytes: 2048, disk_gigabytes: 10},
	"Large (4 vCPU / 8192 MB / 40 GB)": {vcpus: 4, memory_megabytes: 8192, disk_gigabytes: 40},
};


frappe.ui.form.on("Virtual Machine", {
	refresh(frm) {
		if (frm.is_new()) {
			render_creation_messages(frm);
			return;
		}
		add_lifecycle_buttons(frm);
		add_terminated_actions(frm);
		render_dashboard_chips(frm);
		render_status_intro(frm);
		expand_networking_for_pending(frm);
		subscribe_to_realtime(frm);
	},
	size_preset(frm) {
		const preset = SIZE_PRESETS[frm.doc.size_preset];
		if (!preset) return;
		frm.set_value("vcpus", preset.vcpus);
		frm.set_value("memory_megabytes", preset.memory_megabytes);
		frm.set_value("disk_gigabytes", preset.disk_gigabytes);
	},
	server(frm) {
		if (frm.is_new()) {
			render_creation_messages(frm);
		}
	},
	vcpus(frm) {
		if (frm.is_new()) {
			render_creation_messages(frm);
		}
	},
	description(frm) {
		if (frm.is_new()) {
			render_creation_messages(frm);
		}
	},
});


function render_creation_messages(frm) {
	// `set_intro` and `dashboard.set_headline_alert` share one DOM slot
	// (form-message-container) and stack on each call; clearing must happen
	// once at the top, before re-emitting both the description nudge and
	// the capacity headline. The render token discards stale async capacity
	// responses when multiple events (server, vcpus, description) fire in
	// quick succession.
	frm._atlas_creation_render_token = (frm._atlas_creation_render_token || 0) + 1;
	const token = frm._atlas_creation_render_token;
	frm.dashboard.clear_headline();
	render_description_nudge(frm);
	if (frm.doc.server) {
		render_capacity_indicator(frm, token);
	}
}


function render_description_nudge(frm) {
	if (frm.doc.description && frm.doc.description.trim()) {
		return;
	}
	frm.dashboard.set_headline_alert(
		__("Without a description the list will show only a UUID. Add at least a one-word label."),
		"yellow",
	);
}


function render_capacity_indicator(frm, token) {
	// Caller (render_creation_messages) is responsible for clearing the
	// shared headline slot before calling us — we only append. The token
	// gates against late-resolving responses from a prior render cycle.
	frappe.call({
		method: "atlas.atlas.api.server_capacity.capacity_for_server",
		args: {server: frm.doc.server},
	}).then(({message}) => {
		if (!message) return;
		if (token !== frm._atlas_creation_render_token) return;
		const total = message.total_vcpus;
		const used = message.used_vcpus;
		const requested = parseInt(frm.doc.vcpus || 0, 10);
		const projected = used + requested;
		const total_label = total != null ? total : "—";
		const oversubscribed = total != null && projected > total;
		const color = oversubscribed ? "red" : (total != null && projected === total ? "orange" : "blue");
		const text = __(
			"Server capacity: {0} vCPUs requested + {1} used / {2} total ({3} VMs)",
			[requested, used, total_label, message.virtual_machine_count],
		);
		frm.dashboard.set_headline_alert(
			oversubscribed
				? `⚠ ${text} — ${__("Server is oversubscribed. Provision may fail.")}`
				: text,
			color,
		);
	});
}


function add_lifecycle_buttons(frm) {
	const status = frm.doc.status;
	if (status === "Terminated") {
		// Terminated VMs get their own button set (see add_terminated_actions).
		return;
	}
	const primary = PRIMARY_BY_STATUS[status];
	if (primary) {
		frappe.atlas.add_primary(frm, primary.label, () => confirm_lifecycle(frm, primary));
	}
	for (const action of SECONDARY_BY_STATUS[status] || []) {
		frappe.atlas.add_secondary(frm, action.label, () => confirm_lifecycle(frm, action));
	}
	frappe.atlas.add_danger(frm, "Terminate", () => confirm_terminate(frm));
}


function add_terminated_actions(frm) {
	if (frm.doc.status !== "Terminated") return;
	frappe.atlas.add_primary(frm, "Re-provision as new", () => reprovision_as_new(frm));
	frappe.atlas.add_danger(frm, "Delete record", () => confirm_delete(frm));
}


function confirm_lifecycle(frm, action) {
	frappe.confirm(__("{0} {1}?", [action.label, frm.doc.name.slice(0, 8)]), () => {
		frm.call(action.method).then(({message: task_name}) => {
			if (typeof task_name === "string") {
				frappe.atlas.task_started(frm, action.label, task_name);
			} else {
				frm.reload_doc();
			}
		});
	});
}


function confirm_terminate(frm) {
	const short_id = frm.doc.name.slice(0, 8);
	const body = `
		<p>${__("IPv6: {0}", [`<code>${frappe.utils.escape_html(frm.doc.ipv6_address || "—")}</code>`])}</p>
		<p>${__("Image: {0}", [`<b>${frappe.utils.escape_html(frm.doc.image || "—")}</b>`])}</p>
		<p>${__("Server: {0}", [`<b>${frappe.utils.escape_html(frm.doc.server || "—")}</b>`])}</p>
		<p>${__("This deletes the VM's disk artifacts on the host. The UUID and Task history are preserved.")}</p>
	`;
	frappe.atlas.confirm_destructive({
		title: __("Terminate {0}?", [frm.doc.description || short_id]),
		body_html: body,
		match_string: short_id,
		match_label: __("Type the short ID ({0}) to confirm", [short_id]),
		proceed_label: __("Terminate"),
		proceed() {
			frm.call("terminate").then(({message: task_name}) => {
				frappe.atlas.task_started(frm, "Terminate", task_name);
			});
		},
	});
}


function confirm_delete(frm) {
	const short_id = frm.doc.name.slice(0, 8);
	frappe.atlas.confirm_destructive({
		title: __("Delete record for {0}?", [frm.doc.description || short_id]),
		body_html: `
			<p>${__("This removes the Virtual Machine row from Atlas. Task history is preserved (the FK is set null on delete).")}</p>
			<p class="text-muted small">${__("Only available for Terminated VMs.")}</p>
		`,
		match_string: short_id,
		match_label: __("Type the short ID ({0}) to confirm", [short_id]),
		proceed_label: __("Delete record"),
		proceed() {
			frappe.db.delete_doc("Virtual Machine", frm.doc.name).then(() => {
				frappe.show_alert({
					message: __("Deleted {0}.", [short_id]),
					indicator: "green",
				});
				frappe.set_route("List", "Virtual Machine");
			});
		},
	});
}


function reprovision_as_new(frm) {
	const clone = frappe.new_doc("Virtual Machine", {
		server: frm.doc.server,
		image: frm.doc.image,
		vcpus: frm.doc.vcpus,
		memory_megabytes: frm.doc.memory_megabytes,
		disk_gigabytes: frm.doc.disk_gigabytes,
		ssh_public_key: frm.doc.ssh_public_key,
		description: frm.doc.description ? `${frm.doc.description} (clone)` : "",
	});
	if (clone && typeof clone.then === "function") {
		clone.then(() => maybe_alert_cloned());
	} else {
		maybe_alert_cloned();
	}
}


function maybe_alert_cloned() {
	frappe.show_alert({
		message: __("New Virtual Machine prefilled. Review and Save to insert."),
		indicator: "blue",
	}, 5);
}


function render_dashboard_chips(frm) {
	frm.dashboard.clear_headline?.();
	if (frm.doc.ipv6_address) {
		const safe = frappe.utils.escape_html(frm.doc.ipv6_address);
		const indicator_color =
			frm.doc.status === "Running" ? "green" :
			frm.doc.status === "Pending" ? "orange" :
			frm.doc.status === "Failed" ? "red" : "grey";
		frm.dashboard.add_indicator(`IPv6 [${safe}]`, indicator_color);
	}
}


function render_status_intro(frm) {
	frm.set_intro("");
	const status = frm.doc.status;

	if (status === "Terminated") {
		// comment_when() returns HTML, so build the headline as a string and
		// inline the timestamp; otherwise escape_html (from the __() helper)
		// would mangle the <span> tag.
		const when_html = frm.doc.last_stopped
			? frappe.datetime.comment_when(frm.doc.last_stopped)
			: `<span>${__("earlier")}</span>`;
		frm.dashboard.set_headline_alert(
			`⛔ ${__("Terminated")} ${when_html}. ${__("This record is kept for audit; the VM no longer exists.")}`,
			"red",
		);
		return;
	}

	if (status === "Failed" || status === "Pending") {
		// Surface the most recent Failure for provision-vm.sh on this VM.
		frappe.db.get_list("Task", {
			fields: ["name", "subject", "status", "modified", "script"],
			filters: {
				virtual_machine: frm.doc.name,
				status: "Failure",
				script: "provision-vm.sh",
			},
			order_by: "modified desc",
			limit: 1,
		}).then((rows) => {
			if (!rows.length) return;
			const failure = rows[0];
			const subject = failure.subject || failure.name;
			const link = `<a href="/app/task/${encodeURIComponent(failure.name)}">${frappe.utils.escape_html(subject)} →</a>`;
			frm.set_intro(
				__("Last Provision attempt failed — {0}. Fix the cause, then click Provision to retry.", [link]),
				"red",
			);
		});
	}
}


function expand_networking_for_pending(frm) {
	if (frm.doc.status !== "Pending" || !frm.doc.ipv6_address) return;
	// Open the collapsed Networking section so the IPv6 is visible before
	// Provision is clicked. The form's section iterator API varies between
	// Frappe versions; try the most common shapes.
	const section = (cur_frm?.layout?.sections || []).find(
		(s) => s.df && s.df.fieldname === "section_break_networking",
	);
	if (section && typeof section.collapse === "function") {
		section.collapse(false);
	}
}


function subscribe_to_realtime(frm) {
	if (frm._atlas_vm_realtime_registered) return;
	frm._atlas_vm_realtime_registered = true;
	frappe.realtime.on("virtual_machine_update", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		frm.reload_doc();
	});
	// Tasks for this VM also drive form refresh — when a provision Task
	// completes (Failure or Success), the controller updates VM status.
	frappe.realtime.on("task_update", (data) => {
		if (!data || data.virtual_machine !== frm.doc.name) return;
		frm.reload_doc();
	});
}
