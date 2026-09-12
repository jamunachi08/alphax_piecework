import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

from alphax_piecework.engine import audit, payroll
from alphax_piecework.utils import ROLE_MANAGER, assert_role, currency_of, get_settings


class PieceWorkWageBatch(Document):
	def validate(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))
		self.currency = currency_of(self.company)
		if not self.posting_mode:
			self.posting_mode = get_settings().posting_mode
		self.validate_overlap()
		payroll.set_totals(self)
		if self.docstatus == 0:
			self.status = "Draft"

	def validate_overlap(self):
		clash = frappe.db.sql(
			"""select name from `tabPieceWork Wage Batch`
			where docstatus = 1 and company = %(company)s and name != %(name)s
			  and from_date <= %(to_date)s and to_date >= %(from_date)s
			  and ifnull(production_cell, '') = %(cell)s and ifnull(department, '') = %(dept)s limit 1""",
			{"company": self.company, "name": self.name or "", "from_date": self.from_date, "to_date": self.to_date,
			 "cell": self.production_cell or "", "dept": self.department or ""},
		)
		if clash:
			frappe.msgprint(_("Another submitted batch {0} covers an overlapping period. Only unbatched entries are picked, so no double payment occurs.").format(clash[0][0]),
							indicator="blue", alert=True)

	@frappe.whitelist()
	def fetch_entries(self):
		self.check_permission("write")
		if self.docstatus != 0:
			frappe.throw(_("Entries can only be fetched on a draft batch."))
		self.currency = currency_of(self.company)
		count = payroll.build_batch(self)
		self.save()
		frappe.msgprint(_("{0} employee(s), {1} line(s) fetched.").format(count, len(self.details)), alert=True)
		return count

	def before_submit(self):
		if not self.get("employees"):
			frappe.throw(_("Fetch entries before submitting."))
		payroll.assert_sources_still_unbatched(self)
		if self.posting_mode == payroll.MODE_JE:
			from alphax_piecework.utils import require_company_accounts
			require_company_accounts(self.company)
		elif self.posting_mode == payroll.MODE_AS:
			payroll.assert_salary_structures(self)

	def on_submit(self):
		payroll.mark_sources(self, "Batched")
		threshold = cint(get_settings().enqueue_threshold) or 50
		audit.record(self.doctype, self.name, "Submitted", None, None,
					 {"employees": len(self.employees), "total_net": self.total_net, "mode": self.posting_mode})
		if len(self.employees) > threshold:
			self.db_set("status", "Queued")
			frappe.enqueue("alphax_piecework.engine.payroll.post_batch", queue="long", timeout=3600,
						   batch_name=self.name, in_background=True, enqueue_after_commit=True,
						   job_id=f"apw_post_{self.name}", deduplicate=True)
			frappe.msgprint(_("Posting {0} employees in the background.").format(len(self.employees)), alert=True)
		else:
			payroll.post_batch(self.name, in_background=False)

	def before_cancel(self):
		if self.status == "Queued":
			frappe.throw(_("Posting is still running. Try again when it finishes."))
		self.ignore_linked_doctypes = ("Journal Entry", "Additional Salary", "PieceWork Audit Event", "Piece Rate Log", "Shop Floor QA Log")

	def on_cancel(self):
		payroll.reverse_postings(self)
		payroll.mark_sources(self, "Unbatched")
		self.db_set("status", "Cancelled")
		audit.record(self.doctype, self.name, "Cancelled")

	@frappe.whitelist()
	def retry_posting(self):
		assert_role(ROLE_MANAGER)
		if self.docstatus != 1 or self.status not in ("Failed", "Partially Posted"):
			frappe.throw(_("Only failed or partially posted batches can be retried."))
		self.db_set("status", "Queued")
		frappe.enqueue("alphax_piecework.engine.payroll.post_batch", queue="long", timeout=3600,
					   batch_name=self.name, in_background=True, enqueue_after_commit=True,
					   job_id=f"apw_post_{self.name}", deduplicate=True)
		return "Queued"
