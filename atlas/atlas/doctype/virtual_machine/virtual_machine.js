frappe.ui.form.on("Virtual Machine", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		const status = frm.doc.status;
		const allowed = {
			Pending:      ["provision", "delete_vm"],
			Provisioning: [],
			Running:      ["stop", "restart", "delete_vm"],
			Stopped:      ["start", "restart", "delete_vm"],
			Failed:       ["provision", "delete_vm"],
			Archived:     [],
		}[status] ?? [];

		const buttons = {
			provision: ["Provision", "provision"],
			start:     ["Start", "start"],
			stop:      ["Stop", "stop"],
			restart:   ["Restart", "restart"],
			delete_vm: ["Delete", "delete_vm"],
		};
		for (const [action, [label, method]] of Object.entries(buttons)) {
			if (!allowed.includes(action)) continue;
			frm.add_custom_button(label, () => {
				frappe.confirm(`${label} ${frm.doc.name}?`, () => {
					frm.call(method).then(() => frm.reload_doc());
				});
			});
		}
	},
});
