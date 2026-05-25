import json

import frappe
from frappe.model.document import Document

IMMUTABLE_AFTER_INSERT = ("server", "virtual_machine", "script", "variables", "triggered_by")


class Task(Document):
	def validate(self) -> None:
		self._validate_variables_json()
		self._validate_immutability()

	def _validate_variables_json(self) -> None:
		if not self.variables:
			frappe.throw("variables is required")
		try:
			parsed = json.loads(self.variables)
		except json.JSONDecodeError as exception:
			frappe.throw(f"variables must be valid JSON: {exception}")
		if not isinstance(parsed, dict):
			frappe.throw("variables must be a JSON object")

	def _validate_immutability(self) -> None:
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(self, field) != getattr(original, field):
				frappe.throw(f"{field} is read-only after insert")
