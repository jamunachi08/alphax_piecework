import frappe
from frappe import _
from frappe.utils import getdate
from frappe.utils.nestedset import get_ancestors_of

from alphax_piecework.engine.calc import specificity_score

MATRIX_FIELDS = [
	"name", "item_code", "item_group", "workstation_type", "difficulty_class", "standard_rate",
	"overtime_multiplier", "urgent_premium_percent", "multi_skill_bonus_percent", "sam_minutes",
	"currency", "valid_from", "modified", "enable_slabs",
]


def _item_groups(item_code):
	if not item_code:
		return set()
	group = frappe.get_cached_value("Item", item_code, "item_group")
	if not group:
		return set()
	return {group, *get_ancestors_of("Item Group", group)}


def resolve_rate(company, operation, posting_date, item_code=None, workstation_type=None, difficulty_class=None):
	"""Return the best matching submitted Piece Rate Matrix row (frappe._dict) or None.

	Blank key columns are wildcards; the most specific row wins, then the latest
	valid_from, then the most recently modified.
	"""
	posting_date = getdate(posting_date)
	rows = frappe.db.sql(
		"""
		select {fields} from `tabPiece Rate Matrix`
		where docstatus = 1 and company = %(company)s and operation = %(operation)s
		  and valid_from <= %(date)s and (valid_to is null or valid_to >= %(date)s)
		  and (ifnull(item_code, '') = '' or item_code = %(item_code)s)
		  and (ifnull(workstation_type, '') = '' or workstation_type = %(workstation_type)s)
		  and (ifnull(difficulty_class, '') = '' or difficulty_class = %(difficulty_class)s)
		""".format(fields=", ".join(f"`{f}`" for f in MATRIX_FIELDS)),
		{
			"company": company,
			"operation": operation,
			"date": posting_date,
			"item_code": item_code or "",
			"workstation_type": workstation_type or "",
			"difficulty_class": difficulty_class or "",
		},
		as_dict=True,
	)
	if not rows:
		return None
	groups = _item_groups(item_code)
	candidates = [r for r in rows if not r.item_group or r.item_group in groups]
	if not candidates:
		return None
	candidates.sort(
		key=lambda r: (
			specificity_score(r.item_code, r.workstation_type, r.difficulty_class, r.item_group),
			getdate(r.valid_from),
			r.modified,
		),
		reverse=True,
	)
	return candidates[0]


def get_slabs(matrix_name):
	return frappe.get_all(
		"Piece Rate Matrix Slab",
		filters={"parent": matrix_name, "parenttype": "Piece Rate Matrix"},
		fields=["from_qty", "to_qty", "rate_multiplier"],
		order_by="from_qty asc",
	)


@frappe.whitelist()
def preview_rate(company, operation, posting_date, item_code=None, workstation_type=None, difficulty_class=None):
	"""Desk helper: shows which matrix row would apply before saving a log."""
	frappe.has_permission("Piece Rate Matrix", "read", throw=True)
	row = resolve_rate(company, operation, posting_date, item_code, workstation_type, difficulty_class)
	if not row:
		return {"message": _("No submitted Piece Rate Matrix row matches.")}
	return row
