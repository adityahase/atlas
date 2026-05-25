frappe.ui.form.on("Server Provider", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button("Test Connection", () => {
			frm.call("test_connection").then(({message}) => {
				frappe.show_alert({
					message: `OK: ${message.email}`,
					indicator: "green",
				});
			});
		});

		frm.add_custom_button("Provision Server", () => {
			const dialog = new frappe.ui.Dialog({
				title: "Provision Server",
				fields: [
					{
						fieldname: "server_name",
						label: "Server Name",
						fieldtype: "Data",
						reqd: 1,
					},
				],
				primary_action_label: "Provision",
				primary_action(values) {
					frm.call("provision_server", {
						server_name: values.server_name,
					}).then(({message}) => {
						dialog.hide();
						frappe.show_alert({
							message: `Provisioning ${message}; watch the Task list.`,
							indicator: "blue",
						});
						frappe.set_route("Form", "Server", message);
					});
				},
			});
			dialog.show();
		});
	},
});
