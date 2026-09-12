"""Overall Labor Effectiveness = Availability x Performance x Quality.

Availability : hours actually logged / planned hours (distinct working days x standard shift hours)
Performance  : earned hours (accepted pieces x SAM) / hours logged
Quality      : accepted pieces / logged pieces
"""

import frappe
from frappe import _
from frappe.utils import flt

from alphax_piecework.engine.calc import efficiency_percent, ole
from alphax_piecework.utils import get_settings


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by = filters.get("group_by") or "Employee"
	data = get_data(filters, group_by)
	chart = {
		"data": {"labels": [d["label"] for d in data[:20]],
				 "datasets": [{"name": _("OLE %"), "values": [d["ole"] for d in data[:20]]}]},
		"type": "bar", "colors": ["#0F3D56"],
	}
	summary = []
	if data:
		total_hours = sum(d["hours"] for d in data)
		total_earned = sum(d["earned_hours"] for d in data)
		summary = [
			{"label": _("Hours Logged"), "value": flt(total_hours, 1), "datatype": "Float"},
			{"label": _("Earned Hours"), "value": flt(total_earned, 1), "datatype": "Float"},
			{"label": _("Performance %"), "value": efficiency_percent(total_earned * 60, total_hours), "datatype": "Percent",
			 "indicator": "Blue"},
		]
	return get_columns(group_by), data, None, chart, summary


def get_columns(group_by):
	options = {"Employee": "Employee", "Workstation": "Workstation", "Operation": "Operation"}[group_by]
	return [
		{"label": _(group_by), "fieldname": "key", "fieldtype": "Link", "options": options, "width": 140},
		{"label": _("Name"), "fieldname": "label", "fieldtype": "Data", "width": 160},
		{"label": _("Days"), "fieldname": "days", "fieldtype": "Int", "width": 60},
		{"label": _("Planned Hrs"), "fieldname": "planned_hours", "fieldtype": "Float", "width": 100},
		{"label": _("Logged Hrs"), "fieldname": "hours", "fieldtype": "Float", "width": 100},
		{"label": _("Earned Hrs"), "fieldname": "earned_hours", "fieldtype": "Float", "width": 100},
		{"label": _("Pieces"), "fieldname": "pieces", "fieldtype": "Float", "width": 90},
		{"label": _("Rejected"), "fieldname": "rejected", "fieldtype": "Float", "width": 85},
		{"label": _("Availability %"), "fieldname": "availability", "fieldtype": "Percent", "width": 110},
		{"label": _("Performance %"), "fieldname": "performance", "fieldtype": "Percent", "width": 115},
		{"label": _("Quality %"), "fieldname": "quality", "fieldtype": "Percent", "width": 90},
		{"label": _("OLE %"), "fieldname": "ole", "fieldtype": "Percent", "width": 90},
	]


def get_data(filters, group_by):
	shift = flt(get_settings().standard_shift_hours) or 8
	cond = "l.docstatus = 1 and l.review_status != 'Rejected' and l.company = %(company)s and l.posting_date between %(from_date)s and %(to_date)s"
	if filters.get("workstation"):
		cond += " and l.workstation = %(workstation)s"
	if group_by == "Employee":
		rows = frappe.db.sql(
			f"""
			select t.k, max(t.n) n, count(distinct t.d) days, sum(t.h) hours, sum(t.em) earned, sum(t.p) pieces, sum(t.r) rejected from (
				select l.employee k, l.employee_name n, l.posting_date d, l.hours_worked h, l.earned_minutes em,
					l.qty_logged p, l.qty_rejected r
				from `tabPiece Rate Log` l where {cond} and l.worker_type = 'Individual'
				union all
				select s.employee, s.employee_name, l.posting_date, s.hours, l.earned_minutes * s.share_percent / 100,
					l.qty_logged * s.share_percent / 100, l.qty_rejected * s.share_percent / 100
				from `tabPiece Rate Log` l join `tabPiece Rate Log Split` s on s.parent = l.name and s.parenttype = 'Piece Rate Log'
				where {cond} and l.worker_type = 'Production Cell'
			) t group by t.k""", filters, as_dict=True)
	else:
		col = "l.workstation" if group_by == "Workstation" else "l.operation"
		rows = frappe.db.sql(
			f"""select {col} k, {col} n, count(distinct l.posting_date) days, sum(l.hours_worked) hours,
				sum(l.earned_minutes) earned, sum(l.qty_logged) pieces, sum(l.qty_rejected) rejected
			from `tabPiece Rate Log` l where {cond} and {col} is not null group by {col}""", filters, as_dict=True)

	out = []
	for r in rows:
		planned = flt(r.days) * shift
		hours = flt(r.hours)
		availability = min(flt(hours / planned * 100, 1), 100) if planned else 0
		performance = efficiency_percent(r.earned, hours)
		quality = flt((1 - flt(r.rejected) / flt(r.pieces)) * 100, 1) if flt(r.pieces) else 0
		out.append({
			"key": r.k, "label": r.n or r.k, "days": r.days, "planned_hours": flt(planned, 1), "hours": flt(hours, 2),
			"earned_hours": flt(flt(r.earned) / 60, 2), "pieces": flt(r.pieces, 1), "rejected": flt(r.rejected, 1),
			"availability": availability, "performance": performance, "quality": quality,
			"ole": ole(availability, min(performance, 100), quality),
		})
	return sorted(out, key=lambda d: d["ole"], reverse=True)
