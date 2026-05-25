frappe.ui.form.on("Server", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button("Bootstrap", () => {
			frappe.confirm(`Bootstrap ${frm.doc.name}?`, () => {
				frm.call("bootstrap").then(({message}) => {
					frappe.show_alert({
						message: `Bootstrap Task: ${message}`,
						indicator: "blue",
					});
					frm.reload_doc();
				});
			});
		});
		frm.add_custom_button("Run Task", () => {
			frappe.msgprint("Run Task is wired in phase 7");
		});
		frm.add_custom_button("Reboot", () => {
			frappe.msgprint("Reboot is wired in phase 7");
		});
	},
});
