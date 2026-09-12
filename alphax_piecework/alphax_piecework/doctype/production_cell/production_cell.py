import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ProductionCell(Document):
	def validate(self):
		seen = set()
		for row in self.members:
			if row.employee in seen:
				frappe.throw(_("Row {0}: employee {1} is listed twice.").format(row.idx, row.employee))
			seen.add(row.employee)
			if flt(row.skill_weight) <= 0:
				row.skill_weight = 1
			emp_company = frappe.db.get_value("Employee", row.employee, "company")
			if emp_company and emp_company != self.company:
				frappe.throw(_("Row {0}: employee belongs to {1}.").format(row.idx, emp_company))
		if not any(r.is_active for r in self.members):
			frappe.throw(_("A cell needs at least one active member."))
