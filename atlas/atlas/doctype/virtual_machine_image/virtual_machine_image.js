frappe.ui.form.on("Virtual Machine Image", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		if (frm.doc.is_active && frm.doc.rootfs_url) {
			frappe.atlas.add_primary(frm, "Sync to All Servers", () =>
				run_sync_to_all_servers(frm)
			);
		}
		if (frm.doc.is_active) {
			frappe.atlas.add_danger(frm, "Archive", () => confirm_archive(frm));
		}
	},
});

function run_sync_to_all_servers(frm) {
	frappe.show_alert({ message: __("Syncing image to all servers…"), indicator: "blue" });
	frm.call("sync_to_all_servers").then(({ message: task_names }) => {
		const count = (task_names || []).length;
		frappe.show_alert({
			message: __("Syncing to {0} server(s); watch the Task list.", [count]),
			indicator: count ? "green" : "orange",
		});
	});
}

function confirm_archive(frm) {
	frappe.atlas.confirm_archive(frm, {
		match: frm.doc.title || frm.doc.image_name,
		match_label: __("Type the image title to confirm"),
		alert_message: __("Image archived."),
	});
}
