from frappe.utils.password import get_decrypted_password


def get_secret(doctype: str, name: str, fieldname: str) -> str:
	"""Read a Password-type field, decrypted. Single chokepoint so the
	storage backend can be swapped later."""
	return get_decrypted_password(doctype, name, fieldname, raise_exception=True)
