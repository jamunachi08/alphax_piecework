"""Wage batch computation and posting.

Earnings flow: submitted Piece Rate Logs (not rejected / not pending review) +
progressive slab incentives + rework payouts - QC deductions, then statutory
caps and minimum-guarantee top-ups, then posting to GL (Journal Entry accrual) or
to HRMS (Additional Salary). Posting runs in a background job for large batches.
"""

import traceback
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from alphax_piecework.engine import audit, calc
from alphax_piecework.engine.rates import get_slabs
from alphax_piecework.utils import get_settings, hrms_installed, require_company_accounts

MODE_JE = "Journal Entry Accrual"
MODE_AS = "Additional Salary (HRMS)"
MODE_NONE = "None (Report Only)"


# ------------------------------------------------------------------ gather
def _log_filters(batch):
	cond = ["l.docstatus = 1", "l.company = %(company)s", "l.posting_date between %(from_date)s and %(to_date)s",
			"l.payroll_status = 'Unbatched'", "l.review_status in ('Not Required', 'Approved')"]
	if batch.production_cell:
		cond.append("l.production_cell = %(production_cell)s")
	return " and ".join(cond)


def gather_lines(batch):
	"""Return list of line dicts: employee, line_type, reference, amount, pieces, date, operation."""
	params = {"company": batch.company, "from_date": batch.from_date, "to_date": batch.to_date,
			  "production_cell": batch.production_cell}
	logs = frappe.db.sql(
		f"""select l.name, l.worker_type, l.employee, l.production_cell, l.posting_date, l.operation,
			l.qty_logged, l.qty_accepted, l.gross_amount, l.effective_rate, l.piece_rate_matrix
		from `tabPiece Rate Log` l where {_log_filters(batch)} order by l.posting_date, l.name""",
		params, as_dict=True,
	)
	splits = defaultdict(list)
	cell_logs = [l.name for l in logs if l.worker_type == "Production Cell"]
	if cell_logs:
		for s in frappe.get_all("Piece Rate Log Split", filters={"parent": ["in", cell_logs], "parenttype": "Piece Rate Log"},
								fields=["parent", "employee", "share_percent", "amount"]):
			splits[s.parent].append(s)

	def owners(log):
		if log.worker_type == "Individual":
			return [(log.employee, 100.0)]
		return [(s.employee, flt(s.share_percent)) for s in splits.get(log.name, [])]

	lines = []
	slab_groups = defaultdict(list)
	for log in logs:
		for emp, share in owners(log):
			lines.append({"employee": emp, "line_type": "Earning", "reference_doctype": "Piece Rate Log",
						  "reference_name": log.name, "posting_date": log.posting_date, "operation": log.operation,
						  "pieces": flt(log.qty_logged * share / 100.0, 4), "amount": calc.r(flt(log.gross_amount) * share / 100.0)})
		if log.piece_rate_matrix:
			owner_key = log.employee if log.worker_type == "Individual" else f"cell::{log.production_cell}"
			slab_groups[(owner_key, log.posting_date, log.piece_rate_matrix)].append(log)

	# Progressive slabs on daily output per owner & rate row (order-independent).
	slab_cache = {}
	for (owner_key, day, matrix), group in slab_groups.items():
		if matrix not in slab_cache:
			slab_cache[matrix] = get_slabs(matrix) if cint(frappe.db.get_value("Piece Rate Matrix", matrix, "enable_slabs")) else []
		slabs = slab_cache[matrix]
		if not slabs:
			continue
		# Slabs reward good output only: pieces rejected by QC do not climb the ladder.
		total_pieces = sum(flt(l.qty_accepted) for l in group)
		incentive = calc.slab_incentive(total_pieces, flt(group[0].effective_rate), slabs)
		if not incentive:
			continue
		for log in group:
			log_incentive = incentive * flt(log.qty_accepted) / total_pieces
			for emp, share in owners(log):
				lines.append({"employee": emp, "line_type": "Slab Incentive", "reference_doctype": "Piece Rate Log",
							  "reference_name": log.name, "posting_date": day, "operation": log.operation,
							  "pieces": 0, "amount": calc.r(log_incentive * share / 100.0)})

	qa_logs = frappe.db.sql(
		"""select q.name, q.piece_rate_log, q.inspection_date, q.operation, q.disposition, q.rework_employee,
			q.original_deduction, q.material_recovery, q.rework_payout, l.worker_type, l.employee
		from `tabShop Floor QA Log` q join `tabPiece Rate Log` l on l.name = q.piece_rate_log
		where q.docstatus = 1 and q.company = %(company)s and q.payroll_status = 'Unbatched'
		  and l.docstatus = 1 and l.review_status in ('Not Required', 'Approved')
		  and q.inspection_date <= %(to_date)s""" + (" and l.production_cell = %(production_cell)s" if batch.production_cell else ""),
		params, as_dict=True,
	)
	for qa in qa_logs:
		if qa.worker_type == "Production Cell" and qa.piece_rate_log not in splits:
			for s in frappe.get_all("Piece Rate Log Split", filters={"parent": qa.piece_rate_log, "parenttype": "Piece Rate Log"},
									fields=["parent", "employee", "share_percent", "amount"]):
				splits[qa.piece_rate_log].append(s)
		origin = ([(qa.employee, 100.0)] if qa.worker_type == "Individual"
				  else [(s.employee, flt(s.share_percent)) for s in splits.get(qa.piece_rate_log, [])])
		for emp, share in origin:
			for field, line_type in (("original_deduction", "QC Deduction"), ("material_recovery", "Material Recovery")):
				if flt(qa[field]):
					lines.append({"employee": emp, "line_type": line_type, "reference_doctype": "Shop Floor QA Log",
								  "reference_name": qa.name, "posting_date": qa.inspection_date, "operation": qa.operation,
								  "pieces": 0, "amount": -calc.r(flt(qa[field]) * share / 100.0)})
		if flt(qa.rework_payout):
			payees = [(qa.rework_employee, 100.0)] if qa.rework_employee else origin
			for emp, share in payees:
				lines.append({"employee": emp, "line_type": "Rework Earning", "reference_doctype": "Shop Floor QA Log",
							  "reference_name": qa.name, "posting_date": qa.inspection_date, "operation": qa.operation,
							  "pieces": 0, "amount": calc.r(flt(qa.rework_payout) * share / 100.0)})

	if batch.department:
		allowed = set(frappe.get_all("Employee", filters={"department": batch.department}, pluck="name"))
		lines = [ln for ln in lines if ln["employee"] in allowed]
	return lines


# ------------------------------------------------------------------ build
def build_batch(batch):
	settings = get_settings()
	lines = gather_lines(batch)
	accounts = None
	try:
		accounts = require_company_accounts(batch.company)
	except frappe.ValidationError:
		frappe.clear_last_message()
		if (batch.posting_mode or settings.posting_mode) == MODE_JE:
			raise
	if not batch.posting_mode:
		batch.posting_mode = settings.posting_mode
	batch.set("employees", [])
	batch.set("details", [])

	per_emp = defaultdict(lambda: {"pieces": 0.0, "gross": 0.0, "slab": 0.0, "rework": 0.0, "qc": 0.0, "damage": 0.0, "days": set()})
	for ln in lines:
		agg = per_emp[ln["employee"]]
		amt = flt(ln["amount"])
		if ln["line_type"] == "Earning":
			agg["gross"] += amt
			agg["pieces"] += flt(ln["pieces"])
			agg["days"].add(getdate(ln["posting_date"]))
		elif ln["line_type"] == "Slab Incentive":
			agg["slab"] += amt
		elif ln["line_type"] == "Rework Earning":
			agg["rework"] += amt
		else:
			agg["qc"] += -amt
			agg["damage"] += -amt
		batch.append("details", {**ln, "currency": batch.currency})

	emp_meta = {}
	if per_emp:
		emp_fields = ["name", "employee_name", "department"]
		if frappe.get_meta("Employee").has_field("payroll_cost_center"):  # added by HRMS
			emp_fields.append("payroll_cost_center")
		for e in frappe.get_all("Employee", filters={"name": ["in", list(per_emp)]}, fields=emp_fields):
			emp_meta[e.name] = e

	for emp in sorted(per_emp):
		agg = per_emp[emp]
		earnings = agg["gross"] + agg["slab"] + agg["rework"]
		days = len(agg["days"])
		cap_adj = 0.0
		if cint(settings.enable_deduction_caps):
			cap_adj = calc.deduction_cap_adjustment(earnings, agg["damage"], days or 1,
				settings.damage_deduction_cap_days, settings.total_deduction_cap_percent, settings.fixed_daily_wage)
		topup = 0.0
		if cint(settings.enable_minimum_guarantee):
			topup = calc.minimum_topup(earnings, days, settings.minimum_daily_earning)
		net = calc.r(earnings - agg["qc"] + cap_adj + topup)
		remarks = []
		if cap_adj:
			remarks.append(_("Deductions of {0} waived by statutory cap.").format(cap_adj))
		if net < 0:
			remarks.append(_("Deductions exceed earnings by {0}; floored at zero.").format(-net))
			net = 0.0
		meta = emp_meta.get(emp) or frappe._dict()
		batch.append("employees", {
			"employee": emp, "employee_name": meta.get("employee_name"), "department": meta.get("department"),
			"cost_center": meta.get("payroll_cost_center") or (accounts.default_cost_center if accounts else None),
			"days_worked": days, "pieces": flt(agg["pieces"], 2), "gross_earnings": calc.r(agg["gross"]),
			"slab_incentive": calc.r(agg["slab"]), "rework_earnings": calc.r(agg["rework"]),
			"qc_deductions": calc.r(agg["qc"]), "deduction_cap_adjustment": cap_adj, "minimum_topup": topup,
			"net_payable": net, "remarks": "\n".join(remarks), "currency": batch.currency,
		})
	set_totals(batch)
	return len(batch.employees)


def set_totals(batch):
	rows = batch.get("employees") or []
	batch.total_employees = len(rows)
	batch.total_pieces = flt(sum(flt(r.pieces) for r in rows), 2)
	batch.total_gross = calc.r(sum(flt(r.gross_earnings) for r in rows))
	batch.total_slab_incentive = calc.r(sum(flt(r.slab_incentive) for r in rows))
	batch.total_rework_earnings = calc.r(sum(flt(r.rework_earnings) for r in rows))
	batch.total_deductions = calc.r(sum(flt(r.qc_deductions) for r in rows))
	batch.total_cap_adjustment = calc.r(sum(flt(r.deduction_cap_adjustment) for r in rows))
	batch.total_minimum_topup = calc.r(sum(flt(r.minimum_topup) for r in rows))
	batch.total_net = calc.r(sum(flt(r.net_payable) for r in rows))


# ------------------------------------------------------------------ source status
def mark_sources(batch, status):
	logs = {d.reference_name for d in batch.details if d.reference_doctype == "Piece Rate Log"}
	qas = {d.reference_name for d in batch.details if d.reference_doctype == "Shop Floor QA Log"}
	wage_batch = None if status == "Unbatched" else batch.name
	for doctype, names in (("Piece Rate Log", logs), ("Shop Floor QA Log", qas)):
		if names:
			frappe.db.sql(
				f"update `tab{doctype}` set payroll_status=%s, wage_batch=%s where name in %s",
				(status, wage_batch, tuple(names)),
			)


def assert_sources_still_unbatched(batch):
	for doctype in ("Piece Rate Log", "Shop Floor QA Log"):
		names = {d.reference_name for d in batch.details if d.reference_doctype == doctype}
		if not names:
			continue
		taken = frappe.db.sql(
			f"select name, wage_batch from `tab{doctype}` where name in %s and (payroll_status != 'Unbatched' or docstatus != 1) for update",
			(tuple(names),), as_dict=True,
		)
		if taken:
			frappe.throw(_("{0} {1} changed since entries were fetched (batched elsewhere or cancelled). Re-fetch entries.").format(
				doctype, ", ".join(t.name for t in taken[:5])), title=_("Stale Batch"))


def assert_salary_structures(batch):
	"""HRMS only accepts Additional Salary for employees with a submitted Salary Structure
	Assignment effective on the payroll date. Fail fast with the full list instead of a Failed batch."""
	if not hrms_installed():
		frappe.throw(_("Posting Mode 'Additional Salary' requires the HRMS app."))
	employees = [r.employee for r in batch.employees if flt(r.net_payable) > 0]
	if not employees:
		return
	covered = set(frappe.db.sql_list(
		"""select distinct employee from `tabSalary Structure Assignment`
		where docstatus = 1 and company = %s and from_date <= %s and employee in %s""",
		(batch.company, batch.to_date, tuple(employees)),
	))
	missing = [e for e in employees if e not in covered]
	if missing:
		frappe.throw(
			_("These employees have no Salary Structure Assignment effective on {0}: {1}. Assign structures or use Journal Entry Accrual.").format(
				frappe.format(batch.to_date, "Date"), ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else "")),
			title=_("Salary Structure Missing"),
		)


# ------------------------------------------------------------------ posting
def post_batch(batch_name, in_background=True):
	"""Post a submitted batch.

	Inline (small batches, inside the submit transaction): errors propagate so the
	submit is atomic. Background: errors roll back the posting only, the batch is
	flagged Failed with the traceback and can be retried from the form.
	"""
	batch = frappe.get_doc("PieceWork Wage Batch", batch_name)
	log = []
	status = "Posted"
	try:
		if batch.posting_mode == MODE_JE:
			je = make_journal_entry(batch)
			if je:
				batch.db_set("journal_entry", je, update_modified=False)
				log.append(f"Journal Entry {je} submitted.")
		elif batch.posting_mode == MODE_AS:
			payable_rows = len([r for r in batch.employees if flt(r.net_payable) > 0])
			failures = make_additional_salaries(batch, log)
			if failures:
				status = "Failed" if failures >= payable_rows else "Partially Posted"
		else:
			log.append("Report-only mode: nothing posted.")
		mark_sources(batch, "Posted" if status in ("Posted", "Partially Posted") else "Batched")
	except Exception:
		if not in_background:
			raise
		frappe.db.rollback()
		status = "Failed"
		log.append(traceback.format_exc())
		frappe.log_error(title=f"PieceWork Wage Batch {batch_name} posting failed")
	batch.db_set({"status": status, "posting_error": "\n".join(log)[-60000:]}, update_modified=False)
	audit.record(batch.doctype, batch.name, f"Posting {status}", None, None,
				 {"total_net": batch.total_net, "mode": batch.posting_mode, "at": str(now_datetime())})
	if in_background:
		frappe.db.commit()
	return status


def make_journal_entry(batch):
	acc = require_company_accounts(batch.company)
	rows = [r for r in batch.employees if flt(r.net_payable) > 0]
	if not rows:
		return None
	payable_type = frappe.get_cached_value("Account", acc.wage_payable_account, "account_type")
	by_cc = defaultdict(float)
	for r in rows:
		by_cc[r.cost_center or acc.default_cost_center] += flt(r.net_payable)
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = batch.company
	je.posting_date = batch.posting_date
	je.user_remark = _("Piece-rate wages {0} ({1} to {2})").format(batch.name, batch.from_date, batch.to_date)
	for cc, amount in by_cc.items():
		je.append("accounts", {"account": acc.wage_expense_account, "cost_center": cc,
							   "debit_in_account_currency": calc.r(amount), "user_remark": batch.name})
	for r in rows:
		line = {"account": acc.wage_payable_account, "credit_in_account_currency": flt(r.net_payable),
				"cost_center": r.cost_center or acc.default_cost_center, "user_remark": r.employee}
		if payable_type in ("Payable", "Receivable"):
			line.update({"party_type": "Employee", "party": r.employee})
		je.append("accounts", line)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def make_additional_salaries(batch, log):
	if not hrms_installed():
		frappe.throw(_("Posting Mode 'Additional Salary' requires the HRMS app."))
	acc = require_company_accounts(batch.company)
	component = acc.salary_component
	if not component or not frappe.db.exists("Salary Component", component):
		frappe.throw(_("Set a valid Salary Component for {0} in PieceWork Settings.").format(batch.company))
	failures = 0
	for row in batch.employees:
		if flt(row.net_payable) <= 0 or row.additional_salary:
			continue
		try:
			frappe.db.savepoint("apw_as")
			doc = frappe.get_doc({
				"doctype": "Additional Salary", "employee": row.employee, "company": batch.company,
				"salary_component": component, "amount": flt(row.net_payable), "payroll_date": batch.to_date,
				"overwrite_salary_structure_amount": 0, "ref_doctype": "PieceWork Wage Batch", "ref_docname": batch.name,
			})
			doc.flags.ignore_permissions = True
			doc.insert()
			doc.submit()
			row.db_set("additional_salary", doc.name, update_modified=False)
			log.append(f"{row.employee}: Additional Salary {doc.name}")
		except Exception as e:
			frappe.db.rollback(save_point="apw_as")
			frappe.clear_last_message()
			failures += 1
			row.db_set("remarks", ((row.remarks or "") + f"\nPosting failed: {e}").strip(), update_modified=False)
			log.append(f"{row.employee}: FAILED - {e}")
	return failures


def reverse_postings(batch):
	if batch.journal_entry and frappe.db.get_value("Journal Entry", batch.journal_entry, "docstatus") == 1:
		je = frappe.get_doc("Journal Entry", batch.journal_entry)
		je.flags.ignore_permissions = True
		je.cancel()
	for row in batch.employees:
		if row.additional_salary and frappe.db.get_value("Additional Salary", row.additional_salary, "docstatus") == 1:
			doc = frappe.get_doc("Additional Salary", row.additional_salary)
			doc.flags.ignore_permissions = True
			doc.cancel()  # raises if already pulled into a submitted Salary Slip - intended
