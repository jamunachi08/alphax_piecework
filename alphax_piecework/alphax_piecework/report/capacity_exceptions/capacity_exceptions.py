import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	cond = "l.docstatus = 1 and l.capacity_status = 'Exceeded' and l.company = %(company)s and l.posting_date between %(from_date)s and %(to_date)s"
	if filters.get("review_status"):
		cond += " and l.review_status = %(review_status)s"
	rows = frappe.db.sql(
		f"""select l.name log, l.posting_date, l.worker_type, coalesce(l.employee_name, l.production_cell) worker,
			l.workstation, l.operation, l.hours_worked, l.capacity_limit, l.capacity_basis, l.qty_logged, l.review_status,
			l.source, l.owner, l.validation_notes
		from `tabPiece Rate Log` l where {cond} order by l.posting_date desc""",
		filters, as_dict=True,
	)
	for r in rows:
		r["excess_percent"] = flt((flt(r.qty_logged) / flt(r.capacity_limit) - 1) * 100, 1) if flt(r.capacity_limit) else 0
	columns = [
		{"label": _("Log"), "fieldname": "log", "fieldtype": "Link", "options": "Piece Rate Log", "width": 150},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Worker"), "fieldname": "worker", "fieldtype": "Data", "width": 160},
		{"label": _("Workstation"), "fieldname": "workstation", "fieldtype": "Link", "options": "Workstation", "width": 120},
		{"label": _("Operation"), "fieldname": "operation", "fieldtype": "Link", "options": "Operation", "width": 120},
		{"label": _("Hours"), "fieldname": "hours_worked", "fieldtype": "Float", "width": 70},
		{"label": _("Ceiling"), "fieldname": "capacity_limit", "fieldtype": "Float", "width": 85},
		{"label": _("Logged"), "fieldname": "qty_logged", "fieldtype": "Float", "width": 85},
		{"label": _("Excess %"), "fieldname": "excess_percent", "fieldtype": "Percent", "width": 85},
		{"label": _("Basis"), "fieldname": "capacity_basis", "fieldtype": "Data", "width": 150},
		{"label": _("Review"), "fieldname": "review_status", "fieldtype": "Data", "width": 110},
		{"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 90},
		{"label": _("Entered By"), "fieldname": "owner", "fieldtype": "Link", "options": "User", "width": 150},
	]
	return columns, rows
