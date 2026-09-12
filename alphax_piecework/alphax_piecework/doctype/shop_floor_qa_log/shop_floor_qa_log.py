import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from alphax_piecework.engine import calc
from alphax_piecework.utils import get_settings

PERCENT_DEFAULTS = {
	calc.DISP_SCRAP: {"original_payout_percent": "default_scrap_payout_percent",
					  "material_recovery_percent": "material_cost_recovery_percent"},
	calc.DISP_REWORK_ORIGINAL: {"original_payout_percent": "default_rework_original_payout_percent"},
	calc.DISP_REWORK_THIRD: {"third_party_deduction_percent": "default_third_party_deduction_percent",
							 "third_party_payout_percent": "default_third_party_payout_percent"},
}


class ShopFloorQALog(Document):
	def validate(self):
		log = self.get_log()
		self.company = log.company
		self.currency = log.currency
		if self.defect_code and not self.responsibility:
			self.responsibility = frappe.get_cached_value("PieceWork Defect Code", self.defect_code, "responsibility")
		if not self.disposition and self.defect_code:
			self.disposition = frappe.get_cached_value("PieceWork Defect Code", self.defect_code, "default_disposition")
		if not self.disposition:
			frappe.throw(_("Disposition is required (set a default on Defect Code {0}).").format(self.defect_code))
		if not self.responsibility:
			self.responsibility = "Worker"
		self.apply_default_percents()
		self.validate_quantities(log)
		if self.disposition == calc.DISP_REWORK_THIRD:
			if not self.rework_employee:
				frappe.throw(_("Rework Specialist is required for third-party rework."))
			if self.rework_employee == log.employee:
				frappe.throw(_("Rework Specialist must differ from the original worker."))
		self.compute_impact(log)

	def get_log(self):
		log = frappe.get_doc("Piece Rate Log", self.piece_rate_log)
		if log.docstatus != 1:
			frappe.throw(_("Piece Rate Log {0} must be submitted.").format(log.name))
		if log.review_status == "Rejected":
			frappe.throw(_("Piece Rate Log {0} was rejected in review; nothing to inspect.").format(log.name))
		return log

	def apply_default_percents(self):
		if cint(self.defaults_applied) or not self.disposition:
			return
		settings = get_settings()
		for field, key in PERCENT_DEFAULTS.get(self.disposition, {}).items():
			if self.get(field) in (None, ""):
				self.set(field, flt(settings.get(key)))
		self.defaults_applied = 1

	def validate_quantities(self, log):
		if flt(self.qty_failed) <= 0:
			frappe.throw(_("Qty Failed must be greater than zero."))
		if flt(self.qty_inspected) and flt(self.qty_failed) > flt(self.qty_inspected):
			frappe.throw(_("Qty Failed cannot exceed Qty Inspected."))
		already = flt(frappe.db.sql(
			"""select sum(qty_failed) from `tabShop Floor QA Log`
			where piece_rate_log=%s and docstatus=1 and name!=%s""",
			(log.name, self.name or ""),
		)[0][0])
		if already + flt(self.qty_failed) > flt(log.qty_logged):
			frappe.throw(_("Total failed pieces ({0}) would exceed pieces logged ({1}) on {2}.").format(
				already + flt(self.qty_failed), flt(log.qty_logged), log.name), title=_("Over-rejection"))

	def compute_impact(self, log):
		qty_logged = flt(log.qty_logged) or 1
		# unit rate = realised gross per piece (includes OT/premium) so deductions mirror what was paid
		self.unit_rate = flt(flt(log.gross_amount) / qty_logged, 6)
		self.material_unit_cost = 0
		if self.disposition == calc.DISP_SCRAP and log.item_code:
			self.material_unit_cost = flt(frappe.db.get_value("Item", log.item_code, "valuation_rate"))
		impact = calc.qa_financials(
			self.disposition, self.qty_failed, self.unit_rate,
			worker_responsible=(self.responsibility or "Worker") == "Worker",
			original_payout_percent=self.original_payout_percent,
			third_party_deduction_percent=self.third_party_deduction_percent,
			third_party_payout_percent=self.third_party_payout_percent,
			material_unit_cost=self.material_unit_cost,
			material_recovery_percent=self.material_recovery_percent,
		)
		self.original_deduction = impact.original_deduction
		self.material_recovery = impact.material_recovery
		self.rework_payout = impact.rework_payout
		self.total_worker_deduction = impact.total_worker_deduction
		if self.disposition == calc.DISP_REWORK_ORIGINAL and not self.rework_employee and log.worker_type == "Individual":
			self.rework_employee = log.employee

	def on_submit(self):
		self.db_set("payroll_status", "Unbatched")
		frappe.get_doc("Piece Rate Log", self.piece_rate_log).refresh_qc_totals()

	def before_cancel(self):
		if self.payroll_status in ("Batched", "Posted"):
			frappe.throw(_("QA Log is included in Wage Batch {0}. Cancel the batch first.").format(self.wage_batch))

	def on_cancel(self):
		frappe.get_doc("Piece Rate Log", self.piece_rate_log).refresh_qc_totals()


@frappe.whitelist()
def get_percent_defaults(disposition):
	settings = get_settings()
	return {field: flt(settings.get(key)) for field, key in PERCENT_DEFAULTS.get(disposition, {}).items()}
