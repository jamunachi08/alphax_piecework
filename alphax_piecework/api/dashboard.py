"""Shop-floor leaderboard data. No money is exposed - monitors are public spaces."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from alphax_piecework.engine.calc import efficiency_percent
from alphax_piecework.utils import ROLE_DEVICE, ROLE_MANAGER, ROLE_QC, ROLE_SUPERVISOR, assert_role, get_settings


def _mask(emp_id):
	return (emp_id[:3] + "•••" + emp_id[-3:]) if emp_id and len(emp_id) > 6 else "•••"


@frappe.whitelist()
def get_leaderboard(company=None, date=None, workstation=None, limit=15):
	assert_role(ROLE_MANAGER, ROLE_SUPERVISOR, ROLE_QC, ROLE_DEVICE)
	settings = get_settings()
	date = getdate(date or nowdate())
	company = company or frappe.defaults.get_user_default("Company")
	cond = "l.docstatus < 2 and l.review_status != 'Rejected' and l.posting_date = %(date)s"
	if company:
		cond += " and l.company = %(company)s"
	if workstation:
		cond += " and l.workstation = %(workstation)s"
	params = {"date": date, "company": company, "workstation": workstation, "limit": cint(limit) or 15}

	# Individuals + cell members (pieces apportioned by share)
	rows = frappe.db.sql(
		f"""
		select t.employee, t.employee_name, sum(t.pieces) pieces, sum(t.accepted) accepted,
			sum(t.earned) earned, sum(t.hours) hours
		from (
			select l.employee, l.employee_name, l.qty_logged pieces, l.qty_accepted accepted,
				l.earned_minutes earned, l.hours_worked hours
			from `tabPiece Rate Log` l where {cond} and l.worker_type = 'Individual'
			union all
			select s.employee, s.employee_name, l.qty_logged * s.share_percent / 100, l.qty_accepted * s.share_percent / 100,
				l.earned_minutes * s.share_percent / 100, s.hours
			from `tabPiece Rate Log` l join `tabPiece Rate Log Split` s on s.parent = l.name and s.parenttype = 'Piece Rate Log'
			where {cond} and l.worker_type = 'Production Cell'
		) t group by t.employee, t.employee_name order by pieces desc limit %(limit)s
		""",
		params, as_dict=True,
	)
	show_names = cint(settings.leaderboard_show_names)
	board = []
	for i, r in enumerate(rows, 1):
		board.append({
			"rank": i,
			"label": (r.employee_name or r.employee) if show_names else _mask(r.employee),
			"pieces": flt(r.pieces, 0),
			"efficiency": efficiency_percent(r.earned, r.hours),
			"quality": flt(flt(r.accepted) / flt(r.pieces) * 100, 1) if flt(r.pieces) else 0,
		})

	totals = frappe.db.sql(
		f"""select coalesce(sum(qty_logged),0) pieces, coalesce(sum(qty_rejected),0) rejected,
			coalesce(sum(earned_minutes),0) earned, coalesce(sum(hours_worked),0) hours, count(*) logs
		from `tabPiece Rate Log` l where {cond}""", params, as_dict=True)[0]
	hourly = frappe.db.sql(
		f"""select hour(l.to_time) h, sum(l.qty_logged) q from `tabPiece Rate Log` l
		where {cond} and l.to_time is not null group by hour(l.to_time) order by h""", params, as_dict=True)
	cells = frappe.db.sql(
		f"""select l.production_cell label, sum(l.qty_logged) pieces from `tabPiece Rate Log` l
		where {cond} and l.worker_type = 'Production Cell' group by l.production_cell order by pieces desc limit 6""",
		params, as_dict=True)
	return {
		"date": str(date),
		"board": board,
		"cells": cells,
		"totals": {
			"pieces": flt(totals.pieces, 0),
			"quality": flt((1 - flt(totals.rejected) / flt(totals.pieces)) * 100, 1) if flt(totals.pieces) else 0,
			"efficiency": efficiency_percent(totals.earned, totals.hours),
			"logs": totals.logs,
		},
		"hourly": [{"hour": cint(h.h), "qty": flt(h.q)} for h in hourly],
		"refresh_seconds": cint(settings.leaderboard_refresh_seconds) or 30,
	}
