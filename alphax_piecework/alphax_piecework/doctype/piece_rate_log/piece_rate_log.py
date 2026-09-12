import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from alphax_piecework.engine import audit, calc, validation
from alphax_piecework.engine.rates import resolve_rate
from alphax_piecework.utils import ROLE_MANAGER, assert_role, currency_of, get_settings

AUDIT_KEYS = ("employee", "production_cell", "operation", "work_order", "qty_logged", "hours_worked",
			  "overtime_hours", "gross_amount", "from_time", "to_time")


class PieceRateLog(Document):
	def validate(self):
		settings = get_settings()
		self.set_defaults_from_masters()
		validation.validate_time_window(self, settings)
		self.set_cell_members()
		validation.validate_overlap(self, settings)
		validation.validate_skills(self, settings)
		validation.validate_attendance(self, settings)
		self.apply_rate(settings)
		validation.evaluate_capacity(self, settings)
		self.compute_amounts()
		if self.amended_from and not (self.amendment_reason or "").strip():
			frappe.throw(_("Amendment Reason is mandatory when amending an approved log."))

	def before_submit(self):
		settings = get_settings()
		if flt(self.qty_logged) <= 0:
			frappe.throw(_("Pieces Logged must be greater than zero."))
		if not self.piece_rate_matrix:
			frappe.throw(_("No submitted Piece Rate Matrix matches operation {0} on {1}.").format(
				frappe.bold(self.operation), self.posting_date), title=_("Rate Not Found"))
		validation.validate_material_and_flow(self, settings)
		validation.enforce_capacity_on_submit(self, settings)
		self.payroll_status = "Unbatched"

	def on_submit(self):
		if self.amended_from:
			before = frappe.db.get_value("Piece Rate Log", self.amended_from, list(AUDIT_KEYS), as_dict=True)
			audit.record(self.doctype, self.name, "Amended", self.amendment_reason, before,
						 {k: self.get(k) for k in AUDIT_KEYS})
		if self.review_status == "Pending Review":
			audit.record(self.doctype, self.name, "Held for Review", self.validation_notes)

	def before_cancel(self):
		if self.payroll_status in ("Batched", "Posted"):
			frappe.throw(_("Log is included in Wage Batch {0}. Cancel the batch first.").format(self.wage_batch))
		open_qa = frappe.get_all("Shop Floor QA Log", filters={"piece_rate_log": self.name, "docstatus": 1}, pluck="name")
		if open_qa:
			frappe.throw(_("Cancel linked QA Logs first: {0}").format(", ".join(open_qa)))

	def on_cancel(self):
		audit.record(self.doctype, self.name, "Cancelled", None, {k: self.get(k) for k in AUDIT_KEYS})

	# -------------------------------------------------------------- helpers
	def set_defaults_from_masters(self):
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company")
		if self.work_order:
			wo = frappe.db.get_value("Work Order", self.work_order,
				["production_item", "company", "apw_is_urgent"], as_dict=True) or {}
			self.item_code = self.item_code or wo.get("production_item")
			if cint(wo.get("apw_is_urgent")):
				self.is_urgent = 1
		if self.workstation and not self.workstation_type:
			self.workstation_type = frappe.get_cached_value("Workstation", self.workstation, "workstation_type")
		if self.worker_type == "Individual":
			self.production_cell = None
			self.set("splits", [])
		else:
			self.employee = None
			self.employee_name = None
		self.currency = currency_of(self.company) if self.company else self.currency

	def set_cell_members(self):
		if self.worker_type != "Production Cell" or not self.production_cell:
			return
		if not self.get("splits"):
			cell = frappe.get_cached_doc("Production Cell", self.production_cell)
			for m in cell.members:
				if cint(m.is_active):
					self.append("splits", {"employee": m.employee, "employee_name": m.employee_name,
										   "hours": self.hours_worked, "skill_weight": m.skill_weight or 1})
		if not self.get("splits"):
			frappe.throw(_("Production Cell {0} has no active members.").format(self.production_cell))
		seen = set()
		for row in self.splits:
			if row.employee in seen:
				frappe.throw(_("Employee {0} appears twice in the split.").format(row.employee))
			seen.add(row.employee)
			if flt(row.hours) > flt(self.hours_worked) + 0.01 and flt(self.hours_worked):
				frappe.throw(_("Row {0}: member hours exceed log hours.").format(row.idx))

	def apply_rate(self, settings):
		if not (self.company and self.operation and self.posting_date):
			return
		row = resolve_rate(self.company, self.operation, self.posting_date, self.item_code,
						   self.workstation_type, self.difficulty_class)
		if not row:
			self.piece_rate_matrix = None
			self.base_rate = self.effective_rate = 0
			if self.docstatus == 0:
				frappe.msgprint(_("No Piece Rate Matrix matches this log yet."), indicator="orange", alert=True)
			return
		self.piece_rate_matrix = row.name
		self.base_rate = flt(row.standard_rate)
		multiplier = 1.0
		if self.difficulty_class and not row.difficulty_class:
			multiplier = flt(frappe.get_cached_value("PieceWork Difficulty Class", self.difficulty_class, "rate_multiplier")) or 1.0
		self.effective_rate = flt(self.base_rate * multiplier, 6)
		self.overtime_multiplier = flt(row.overtime_multiplier) or 1.0
		self.urgent_premium_percent = flt(row.urgent_premium_percent) if cint(self.is_urgent) else 0
		bonus = flt(row.multi_skill_bonus_percent)
		self.multi_skill_bonus_percent = flt(bonus * validation.multi_skill_eligible_ratio(self, settings), 4) if bonus else 0
		self.sam_minutes = flt(row.sam_minutes) or flt(frappe.get_cached_value("Operation", self.operation, "apw_default_sam_minutes"))
		if row.currency:
			self.currency = row.currency

	def compute_amounts(self):
		e = calc.compute_earnings(
			self.qty_logged, self.effective_rate, self.hours_worked, self.overtime_hours,
			self.overtime_multiplier, self.urgent_premium_percent, self.multi_skill_bonus_percent,
		)
		self.regular_amount, self.overtime_amount = e.regular_amount, e.overtime_amount
		self.premium_amount, self.gross_amount = e.premium_amount, e.gross_amount
		self.qty_rejected = flt(self.qty_rejected)
		self.qty_accepted = flt(self.qty_logged) - self.qty_rejected
		self.earned_minutes = flt(self.qty_accepted * flt(self.sam_minutes), 2)
		self.net_amount = calc.r(flt(self.gross_amount) - flt(self.qc_deduction))
		if self.worker_type == "Production Cell" and self.get("splits"):
			cell_method = frappe.get_cached_value("Production Cell", self.production_cell, "split_method") or calc.SPLIT_HOURS
			shares = calc.split_amount(self.gross_amount, [
				{"employee": r.employee, "hours": r.hours, "skill_weight": r.skill_weight} for r in self.splits
			], cell_method)
			for row, share in zip(self.splits, shares):
				row.share_percent = share["share_percent"]
				row.amount = share["amount"]
				row.currency = self.currency

	# -------------------------------------------------------------- QC roll-up
	def refresh_qc_totals(self):
		totals = frappe.db.sql(
			"""select coalesce(sum(qty_failed), 0), coalesce(sum(total_worker_deduction), 0)
			from `tabShop Floor QA Log` where piece_rate_log = %s and docstatus = 1""",
			self.name,
		)[0]
		rejected, deduction = flt(totals[0]), flt(totals[1])
		accepted = flt(self.qty_logged) - rejected
		self.db_set({
			"qty_rejected": rejected,
			"qty_accepted": accepted,
			"qc_deduction": deduction,
			"net_amount": calc.r(flt(self.gross_amount) - deduction),
			"earned_minutes": flt(accepted * flt(self.sam_minutes), 2),
		}, update_modified=False)


@frappe.whitelist()
def review_log(name, decision, reason):
	"""Manager decision on a log held for capacity review. Enforces segregation of duties."""
	assert_role(ROLE_MANAGER)
	if decision not in ("Approved", "Rejected"):
		frappe.throw(_("Invalid decision."))
	if not (reason or "").strip():
		frappe.throw(_("A reason is mandatory."))
	doc = frappe.get_doc("Piece Rate Log", name)
	doc.check_permission("write")
	if doc.docstatus != 1 or doc.review_status != "Pending Review":
		frappe.throw(_("Only submitted logs pending review can be decided."))
	if doc.owner == frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("You cannot review a log you created (segregation of duties)."))
	before = {"review_status": doc.review_status, "net_amount": doc.net_amount}
	updates = {"review_status": decision,
			   "validation_notes": ((doc.validation_notes or "") + f"\n{decision}: {reason}").strip()}
	doc.db_set(updates)
	audit.record(doc.doctype, doc.name, f"Capacity Review {decision}", reason, before, updates)
	doc.add_comment("Info", _("Capacity review {0}: {1}").format(decision, reason))
	return decision
