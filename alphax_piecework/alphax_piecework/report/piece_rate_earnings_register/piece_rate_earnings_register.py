import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Log"), "fieldname": "log", "fieldtype": "Link", "options": "Piece Rate Log", "width": 150},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 120},
		{"label": _("Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
		{"label": _("Cell"), "fieldname": "production_cell", "fieldtype": "Link", "options": "Production Cell", "width": 110},
		{"label": _("Operation"), "fieldname": "operation", "fieldtype": "Link", "options": "Operation", "width": 120},
		{"label": _("Work Order"), "fieldname": "work_order", "fieldtype": "Link", "options": "Work Order", "width": 140},
		{"label": _("Pieces"), "fieldname": "pieces", "fieldtype": "Float", "width": 90},
		{"label": _("Rejected"), "fieldname": "rejected", "fieldtype": "Float", "width": 85},
		{"label": _("Gross"), "fieldname": "gross", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("QC Deduction"), "fieldname": "qc_deduction", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("Net"), "fieldname": "net", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("Review"), "fieldname": "review_status", "fieldtype": "Data", "width": 110},
		{"label": _("Payroll"), "fieldname": "payroll_status", "fieldtype": "Data", "width": 90},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def get_data(filters):
	cond = ["l.docstatus = 1", "l.company = %(company)s", "l.posting_date between %(from_date)s and %(to_date)s"]
	for key in ("operation", "production_cell", "payroll_status"):
		if filters.get(key):
			cond.append(f"l.{key} = %({key})s")
	where = " and ".join(cond)
	emp_ind = " and l.employee = %(employee)s" if filters.get("employee") else ""
	emp_cell = " and s.employee = %(employee)s" if filters.get("employee") else ""
	return frappe.db.sql(
		f"""
		select * from (
			select l.posting_date, l.name log, l.employee, l.employee_name, l.production_cell, l.operation, l.work_order,
				l.qty_logged pieces, l.qty_rejected rejected, l.gross_amount gross, l.qc_deduction, l.net_amount net,
				l.review_status, l.payroll_status, l.currency
			from `tabPiece Rate Log` l where {where} and l.worker_type = 'Individual' {emp_ind}
			union all
			select l.posting_date, l.name, s.employee, s.employee_name, l.production_cell, l.operation, l.work_order,
				l.qty_logged * s.share_percent / 100, l.qty_rejected * s.share_percent / 100, s.amount,
				l.qc_deduction * s.share_percent / 100, s.amount - (l.qc_deduction * s.share_percent / 100),
				l.review_status, l.payroll_status, l.currency
			from `tabPiece Rate Log` l join `tabPiece Rate Log Split` s on s.parent = l.name and s.parenttype = 'Piece Rate Log'
			where {where} and l.worker_type = 'Production Cell' {emp_cell}
		) t order by posting_date, log
		""",
		filters, as_dict=True,
	)
