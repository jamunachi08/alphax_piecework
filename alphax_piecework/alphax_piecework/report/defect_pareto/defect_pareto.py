import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	cond = "q.docstatus = 1 and q.company = %(company)s and q.inspection_date between %(from_date)s and %(to_date)s"
	if filters.get("operation"):
		cond += " and q.operation = %(operation)s"
	rows = frappe.db.sql(
		f"""select q.defect_code, d.defect_name, d.defect_name_ar, d.category, d.responsibility,
			count(*) occurrences, sum(q.qty_failed) qty_failed, sum(q.total_worker_deduction) worker_deduction,
			sum(q.rework_payout) rework_cost
		from `tabShop Floor QA Log` q left join `tabPieceWork Defect Code` d on d.name = q.defect_code
		where {cond} group by q.defect_code order by qty_failed desc""",
		filters, as_dict=True,
	)
	total = sum(flt(r.qty_failed) for r in rows) or 1
	running = 0
	for r in rows:
		running += flt(r.qty_failed)
		r["share"] = flt(flt(r.qty_failed) / total * 100, 1)
		r["cumulative"] = flt(running / total * 100, 1)
		r["vital_few"] = 1 if r["cumulative"] - r["share"] < 80 else 0
	columns = [
		{"label": _("Defect Code"), "fieldname": "defect_code", "fieldtype": "Link", "options": "PieceWork Defect Code", "width": 120},
		{"label": _("Defect"), "fieldname": "defect_name", "fieldtype": "Data", "width": 190},
		{"label": _("Defect (AR)"), "fieldname": "defect_name_ar", "fieldtype": "Data", "width": 170},
		{"label": _("Category"), "fieldname": "category", "fieldtype": "Data", "width": 105},
		{"label": _("Responsibility"), "fieldname": "responsibility", "fieldtype": "Data", "width": 120},
		{"label": _("Occurrences"), "fieldname": "occurrences", "fieldtype": "Int", "width": 100},
		{"label": _("Qty Failed"), "fieldname": "qty_failed", "fieldtype": "Float", "width": 95},
		{"label": _("Share %"), "fieldname": "share", "fieldtype": "Percent", "width": 85},
		{"label": _("Cumulative %"), "fieldname": "cumulative", "fieldtype": "Percent", "width": 105},
		{"label": _("Vital Few (80/20)"), "fieldname": "vital_few", "fieldtype": "Check", "width": 110},
		{"label": _("Worker Deductions"), "fieldname": "worker_deduction", "fieldtype": "Currency", "width": 130},
		{"label": _("Rework Cost"), "fieldname": "rework_cost", "fieldtype": "Currency", "width": 110},
	]
	chart = {
		"data": {"labels": [r.defect_code for r in rows[:12]],
				 "datasets": [{"name": _("Qty Failed"), "chartType": "bar", "values": [flt(r.qty_failed) for r in rows[:12]]},
							  {"name": _("Cumulative %"), "chartType": "line", "values": [r["cumulative"] for r in rows[:12]]}]},
		"type": "axis-mixed", "colors": ["#0F3D56", "#F2B233"],
	}
	return columns, rows, None, chart
