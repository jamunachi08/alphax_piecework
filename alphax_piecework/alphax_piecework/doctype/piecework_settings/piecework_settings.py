import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from alphax_piecework.utils import hrms_installed


class PieceWorkSettings(Document):
	def validate(self):
		if flt(self.max_efficiency_percent) and flt(self.max_efficiency_percent) < 100:
			frappe.throw(_("Max Plausible Efficiency should be at least 100 %."))
		if self.posting_mode == "Additional Salary (HRMS)" and not hrms_installed():
			frappe.throw(_("Additional Salary posting requires the HRMS app."))
		seen = set()
		for row in self.company_accounts or []:
			if row.company in seen:
				frappe.throw(_("Company {0} is listed twice in Company Accounts.").format(row.company))
			seen.add(row.company)
			for field in ("wage_expense_account", "wage_payable_account"):
				acc_company = frappe.db.get_value("Account", row.get(field), "company")
				if acc_company and acc_company != row.company:
					frappe.throw(_("Row {0}: {1} belongs to {2}.").format(row.idx, row.get(field), acc_company))
			if self.posting_mode == "Additional Salary (HRMS)":
				if not row.salary_component or not frappe.db.exists("Salary Component", row.salary_component):
					frappe.throw(_("Row {0}: a valid Salary Component is required.").format(row.idx))
		if flt(self.total_deduction_cap_percent) > 100:
			frappe.throw(_("Total Deductions Cap cannot exceed 100 %."))

	def on_update(self):
		frappe.clear_cache(doctype="PieceWork Settings")
