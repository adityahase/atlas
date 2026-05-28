frappe.listview_settings["Task"] = {
	add_fields: ["status", "script", "duration_milliseconds"],

	get_indicator(doc) {
		const config = {
			Pending: ["Pending", "grey", "status,=,Pending"],
			Running: ["Running", "yellow", "status,=,Running"],
			Success: ["Success", "green", "status,=,Success"],
			Failure: ["Failure", "red", "status,=,Failure"],
		}[doc.status];
		return config ? [__(config[0]), config[1], config[2]] : null;
	},

	formatters: {
		subject(value, _df, doc) {
			const ms = doc.duration_milliseconds;
			const duration = ms ? `${Math.round(ms / 1000)}s` : "—";
			const label = value || doc.script || doc.name;
			return `${label} · ${duration}`;
		},
	},
};
