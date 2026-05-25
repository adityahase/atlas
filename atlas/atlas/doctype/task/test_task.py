import json

import frappe
from frappe.tests import IntegrationTestCase


class TestTask(IntegrationTestCase):
	def _make(self, **overrides) -> "frappe.model.document.Document":
		defaults = {
			"doctype": "Task",
			"server": None,
			"script": "noop.sh",
			"variables": json.dumps({"FOO": "bar"}),
			"status": "Pending",
			"triggered_by": "Administrator",
		}
		defaults.update(overrides)
		return frappe.get_doc(defaults).insert(ignore_permissions=True)

	def test_task_insert_defaults(self) -> None:
		task = self._make(server=None, script="echo.sh")
		self.assertEqual(task.status, "Pending")
		self.assertEqual(task.script, "echo.sh")
		self.assertIsNone(task.exit_code)

	def test_task_variables_must_be_json(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Task",
				"script": "noop.sh",
				"variables": "{not json",
				"status": "Pending",
				"triggered_by": "Administrator",
			}).insert(ignore_permissions=True)

	def test_task_immutable_after_insert(self) -> None:
		task = self._make()
		task.script = "different.sh"
		with self.assertRaises(frappe.ValidationError):
			task.save(ignore_permissions=True)
