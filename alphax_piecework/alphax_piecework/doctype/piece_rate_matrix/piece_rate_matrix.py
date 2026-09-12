import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from alphax_piecework.engine import audit
from alphax_piecework.engine.calc import specificity_score
from alphax_piecework.utils import currency_of

KEY_FIELDS = ("company", "operation", "item_code", "item_group", "workstation_type", "difficulty_class")
RATE_FIELDS = ("standard_rate", "overtime_multiplier", "urgent_premium_percent", "multi_skill_bonus_percent", "sam_minutes", "valid_from", "valid_to")


class PieceRateMatrix(Document):
	def validate(self):
		if flt(self.standard_rate) <= 0:
			frappe.throw(_("Standard Rate must be greater than zero."))
		if self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("Valid To cannot be before Valid From."))
		if flt(self.overtime_multiplier) and flt(self.overtime_multiplier) < 1:
			frappe.throw(_("Overtime Multiplier cannot be below 1."))
		if self.item_code and self.item_group:
			frappe.throw(_("Set either Item or Item Group, not both."))
		self.currency = self.currency or currency_of(self.company)
		self.specificity_score = specificity_score(self.item_code, self.workstation_type, self.difficulty_class, self.item_group)
		self.title = " / ".join(filter(None, [self.operation, self.item_code or self.item_group, self.workstation_type, self.difficulty_class]))
		self.validate_slabs()
		if self.amended_from and not (self.change_reason or "").strip():
			frappe.throw(_("Reason for Change is mandatory on amendments."))

	def validate_slabs(self):
		if not self.enable_slabs:
			return
		rows = sorted(self.slabs or [], key=lambda r: flt(r.from_qty))
		for i, row in enumerate(rows):
			if flt(row.to_qty) and flt(row.to_qty) <= flt(row.from_qty):
				frappe.throw(_("Slab row {0}: To Qty must exceed From Qty.").format(row.idx))
			if flt(row.rate_multiplier) < 1:
				frappe.throw(_("Slab row {0}: multiplier below 1 would reduce base pay.").format(row.idx))
			if i and flt(rows[i - 1].to_qty) and flt(row.from_qty) < flt(rows[i - 1].to_qty):
				frappe.throw(_("Slabs overlap at row {0}.").format(row.idx))
			if i < len(rows) - 1 and not flt(row.to_qty):
				frappe.throw(_("Only the last slab may be open-ended."))

	def before_submit(self):
		key_sql = " and ".join(f"ifnull(`{k}`, '') = %({k})s" for k in KEY_FIELDS)
		params = {k: self.get(k) or "" for k in KEY_FIELDS}
		params.update({"name": self.name, "valid_from": self.valid_from, "valid_to": self.valid_to or "9999-12-31",
					   "amended_from": self.amended_from or ""})
		clash = frappe.db.sql(
			f"""select name from `tabPiece Rate Matrix` where docstatus = 1 and name != %(name)s and name != %(amended_from)s
			and {key_sql} and valid_from <= %(valid_to)s and ifnull(valid_to, '9999-12-31') >= %(valid_from)s limit 1""",
			params,
		)
		if clash:
			frappe.throw(_("Overlaps with submitted rate {0} for the same scope. Set its Valid To first, or amend it.").format(
				frappe.bold(clash[0][0])), title=_("Overlapping Rate"))

	def on_submit(self):
		before = None
		if self.amended_from:
			before = frappe.db.get_value("Piece Rate Matrix", self.amended_from, list(RATE_FIELDS), as_dict=True)
		audit.record(self.doctype, self.name, "Rate Amended" if self.amended_from else "Rate Published",
					 self.change_reason, before, {k: self.get(k) for k in RATE_FIELDS})

	def on_cancel(self):
		audit.record(self.doctype, self.name, "Rate Withdrawn", None, {k: self.get(k) for k in RATE_FIELDS})
