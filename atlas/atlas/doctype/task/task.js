frappe.ui.form.on("Task", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.disable_save();
		}
	},
});
