frappe.ui.form.on("Virtual Machine Image", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button("Sync to All Servers", () => {
			frm.call("sync_to_all_servers").then(({message}) => {
				frappe.show_alert({
					message: `Enqueued ${message.length} sync Task(s).`,
					indicator: "blue",
				});
			});
		});

		frm.add_custom_button("Sync to Server", () => {
			const dialog = new frappe.ui.Dialog({
				title: "Sync to Server",
				fields: [
					{
						fieldname: "server_name",
						label: "Server",
						fieldtype: "Link",
						options: "Server",
						reqd: 1,
					},
				],
				primary_action_label: "Sync",
				primary_action(values) {
					frm.call("sync_to_server", {server_name: values.server_name})
						.then(({message}) => {
							dialog.hide();
							frappe.set_route("Form", "Task", message);
						});
				},
			});
			dialog.show();
		});
	},
});
