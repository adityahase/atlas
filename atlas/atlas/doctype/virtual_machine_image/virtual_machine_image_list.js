frappe.listview_settings["Virtual Machine Image"] = {
	add_fields: ["is_active", "description"],

	get_indicator(doc) {
		if (!doc.is_active) {
			return [__("Inactive"), "grey", "is_active,=,0"];
		}
		return [__("Active"), "green", "is_active,=,1"];
	},

	formatters: {
		image_name(value, _df, doc) {
			if (!doc.description) return value;
			return `${value} · ${doc.description}`;
		},
	},
};
