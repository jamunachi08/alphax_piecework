import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class EmployeeOperationSkill(Document):
	def validate(self):
		if self.valid_till and self.certified_on and getdate(self.valid_till) < getdate(self.certified_on):
			frappe.throw(_("Valid Till cannot be before Certified On."))
		if not (0 < flt(self.capacity_factor_percent) <= 200):
			self.capacity_factor_percent = 100
		dup = frappe.db.exists("Employee Operation Skill", {"employee": self.employee, "operation": self.operation,
															 "is_active": 1, "name": ["!=", self.name]})
		if dup and self.is_active:
			frappe.throw(_("An active skill record already exists: {0}").format(dup))
