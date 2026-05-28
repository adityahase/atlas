const LOCKED_AFTER_SYNC = [
	"kernel_url",
	"kernel_filename",
	"kernel_sha256",
	"rootfs_url",
	"rootfs_filename",
	"rootfs_sha256",
];


frappe.ui.form.on("Virtual Machine Image", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frappe.atlas.add_primary(frm, "Sync to Server", () => open_sync_to_server_dialog(frm));
		frappe.atlas.add_action(frm, "Sync to All Servers", () => confirm_sync_to_all(frm));
		render_sync_status_panel(frm);
		enforce_lock_state(frm);
	},
});


function render_sync_status_panel(frm) {
	const field = frm.fields_dict.sync_status_html;
	if (!field || !field.$wrapper) return;
	field.$wrapper.html(`<div class="text-muted small">${__("Loading sync status…")}</div>`);
	frm.call("sync_status").then(({message: rows}) => {
		if (!rows || !rows.length) {
			field.$wrapper.html(
				`<div class="text-muted small">${__("No active servers.")}</div>`,
			);
			return;
		}
		// Reusing `.quick-list-widget-box` / `.quick-list-item` lets these
		// rows pick up the same padding, hover, ellipsis, and pill spacing
		// as the workspace `quick_list` widget — without any new CSS.
		const $body = $(
			`<div class="quick-list-widget-box atlas-sync-status-list">
				<div class="widget-body"></div>
			</div>`,
		);
		const $list = $body.find(".widget-body");
		for (const row of rows) {
			const server_name = frappe.utils.escape_html(row.server);
			const region_html = row.region
				? ` <span class="text-muted small">${frappe.utils.escape_html(row.region)}</span>`
				: "";
			const synced = !!row.task;
			const pill_color = synced ? "green" : "gray";
			const pill_label = synced ? __("Synced") : __("Never");
			// `comment_when` returns an HTML <span> with relative-time tooltip;
			// embed directly (do not html-escape).
			const time_html = synced ? frappe.datetime.comment_when(row.synced_at) : "";
			const action_html = synced
				? ""
				: `<a href="#" class="small atlas-sync-now" data-server="${server_name}">${__("Sync now")} →</a>`;
			const $item = $(`
				<div class="quick-list-item">
					<div class="ellipsis left">
						<div class="ellipsis title">${server_name}${region_html}</div>
						<div class="timestamp text-muted">${time_html}${time_html && action_html ? " " : ""}${action_html}</div>
					</div>
					<div class="status indicator-pill ${pill_color} ellipsis">${pill_label}</div>
				</div>
			`);
			if (synced) {
				$item.on("click", (event) => {
					// Let inner anchors (e.g. future drill-ins) handle their own clicks.
					if (event.target.closest("a")) return;
					frappe.set_route("Form", "Task", row.task);
				});
			}
			$list.append($item);
		}
		field.$wrapper.empty().append($body);
		field.$wrapper.off("click.atlas-sync-now").on("click.atlas-sync-now", ".atlas-sync-now", (event) => {
			event.preventDefault();
			const server = event.currentTarget.dataset.server;
			if (!server) return;
			open_sync_to_server_dialog(frm, server);
		});
	});
}


function enforce_lock_state(frm) {
	// Server-side validate() also blocks the change; the client read-only
	// flag is just an early hint so the operator doesn't get a save-time
	// error after editing four fields.
	frappe.db.exists("Task", {
		script: "sync-image.sh",
		status: "Success",
		variables: ["like", `%"IMAGE_NAME": "${frm.doc.name}"%`],
	}).then((exists) => {
		if (!exists) return;
		for (const fieldname of LOCKED_AFTER_SYNC) {
			frm.set_df_property(fieldname, "read_only", 1);
		}
		frm.set_intro(
			__("This image has been synced. To change kernel or rootfs, create a new image (e.g. {0}-v2). Editing here would invalidate prior audit rows.", [frm.doc.name]),
			"blue",
		);
	});
}


function open_sync_to_server_dialog(frm, prefilled_server) {
	const dialog = new frappe.ui.Dialog({
		title: __("Sync to Server"),
		fields: [
			{
				fieldname: "server_name",
				label: __("Server"),
				fieldtype: "Link",
				options: "Server",
				only_select: 1,
				reqd: 1,
				default: prefilled_server || "",
				get_query: () => ({filters: {status: "Active"}}),
			},
			{
				fieldname: "hint",
				fieldtype: "HTML",
				options: `<div class="text-muted small">${__("Each download takes a few minutes per server depending on image size.")}</div>`,
			},
		],
		primary_action_label: __("Sync"),
		primary_action(values) {
			frm.call("sync_to_server", {server_name: values.server_name})
				.then(({message: task_name}) => {
					dialog.hide();
					frappe.atlas.task_started(frm, "Sync image", task_name);
				});
		},
	});
	dialog.show();
}


function confirm_sync_to_all(frm) {
	frappe.db.get_list("Server", {
		fields: ["name", "region"],
		filters: {status: "Active"},
		order_by: "name asc",
		limit: 100,
	}).then((servers) => {
		if (!servers.length) {
			frappe.show_alert({
				message: __("No active servers to sync to."),
				indicator: "orange",
			});
			return;
		}
		const options = servers.map((server) => ({
			label: server.region ? `${server.name} (${server.region})` : server.name,
			value: server.name,
			checked: 1,
		}));
		const dialog = new frappe.ui.Dialog({
			title: __("Sync to active servers"),
			fields: [
				{
					fieldname: "image_intro",
					fieldtype: "HTML",
					options: `<p>${__("Image: {0}", [`<b>${frappe.utils.escape_html(frm.doc.image_name || frm.doc.name)}</b>`])}</p>`,
				},
				{
					fieldname: "targets",
					fieldtype: "MultiCheck",
					label: __("Targets"),
					options: options,
					columns: 1,
				},
				{
					fieldname: "footer_hint",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${__("Each download fetches kernel + rootfs over the public internet, verifies SHA-256, and runs sync-image.sh.")}</p>`,
				},
			],
			primary_action_label: __("Sync"),
			primary_action(values) {
				const targets = values.targets || [];
				if (!targets.length) {
					frappe.show_alert({
						message: __("Pick at least one server."),
						indicator: "orange",
					});
					return;
				}
				dialog.hide();
				frm.call("sync_to_all_servers", {servers: targets}).then(({message}) => {
					frappe.show_alert({
						message: __("Enqueued {0} sync Task(s).", [message.length]),
						indicator: "blue",
					});
				});
			},
		});
		dialog.show();
	});
}
