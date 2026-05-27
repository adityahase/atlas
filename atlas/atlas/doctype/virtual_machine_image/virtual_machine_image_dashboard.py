from frappe import _


def get_data():
	return {
		"fieldname": "image",
		"transactions": [
			{"label": _("Workloads"), "items": ["Virtual Machine"]},
		],
	}
