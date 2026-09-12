"""End-to-end smoke scenario for a live ERPNext v15 site.

Creates its own tagged master data (new employees, operations, department per run),
so it can be re-run on the same site. It DOES write data and commit - only run it on
a test / staging site that has `"allow_tests": true` in site_config.json:

    bench --site staging.local execute alphax_piecework.tests.e2e_scenario.run

Covers: rate resolution, capacity ceilings + review queue + segregation of duties,
overlap detection, QC routing (worker vs machine fault), progressive slabs on accepted
pieces, cell splits, Work Order flow & material balance, wage batch build/JE posting,
stale-batch guard, cancellation reversal, hash-chained audit, edge ingest dedup and
aggregation, script reports, leaderboard API, print format rendering, and (when HRMS
is installed) Additional Salary posting.
"""

import json
import traceback

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, random_string, today

RESULTS = []


def check(name, condition, detail=""):
	RESULTS.append((bool(condition), name, detail))
	print(("  PASS  " if condition else "  FAIL  ") + name + (f"  [{detail}]" if detail and not condition else ""))


def expect_error(name, fn, contains=None):
	frappe.db.savepoint("apw_e2e")
	try:
		fn()
	except Exception as e:
		frappe.db.rollback(save_point="apw_e2e")
		frappe.clear_last_message()
		msg = str(e)
		ok = contains is None or contains.lower() in msg.lower()
		check(name, ok, f"got: {msg[:160]}")
		return
	frappe.db.rollback(save_point="apw_e2e")
	check(name, False, "no error raised")


class Ctx(frappe._dict):
	pass


# ------------------------------------------------------------------ fixtures
def setup(ctx):
	company = frappe.get_all("Company", pluck="name", limit=1)[0]
	abbr = frappe.get_cached_value("Company", company, "abbr")
	ctx.company, ctx.abbr = company, abbr
	ctx.tag = random_string(5).upper()
	ctx.day = add_days(today(), -1)

	settings = frappe.get_doc("PieceWork Settings")
	settings.capacity_action = "Hold for Review"
	settings.capacity_basis = "Stricter of Both"
	settings.max_efficiency_percent = 130
	settings.posting_mode = "Journal Entry Accrual"
	settings.enable_deduction_caps = 1
	settings.enable_minimum_guarantee = 0
	settings.require_time_window = 1
	settings.block_overlapping_logs = 1
	settings.enable_material_balance = 1
	settings.enable_flow_balance = 1
	settings.enable_job_card_bridge = 0
	if not any(r.company == company for r in settings.company_accounts):
		settings.append("company_accounts", {
			"company": company, "wage_expense_account": f"Salary - {abbr}",
			"wage_payable_account": f"Payroll Payable - {abbr}", "default_cost_center": f"Main - {abbr}",
		})
	settings.save()

	dept = frappe.get_doc({"doctype": "Department", "department_name": f"APW Line {ctx.tag}", "company": company}).insert()
	ctx.dept = dept.name

	def emp(first):
		return frappe.get_doc({
			"doctype": "Employee", "first_name": f"{first} {ctx.tag}", "gender": "Male", "date_of_birth": "1990-01-01",
			"date_of_joining": "2024-01-01", "company": company, "status": "Active", "department": ctx.dept,
			"apw_piece_rate_eligible": 1,
		}).insert().name

	ctx.A, ctx.B, ctx.C, ctx.D, ctx.E, ctx.F = (emp(n) for n in ("Ahmed", "Bilal", "Chand", "Dawood", "Imran", "Faisal"))

	for op, sam in (("CUT", 0.25), ("STITCH", 0.5), ("PACK", 0.2)):
		name = f"APW-{op}-{ctx.tag}"
		frappe.get_doc({"doctype": "Operation", "name": name, "apw_default_sam_minutes": sam}).insert()
		ctx[op] = name
	ctx.WS = frappe.get_doc({"doctype": "Workstation", "workstation_name": f"APW-WS-{ctx.tag}",
							 "apw_max_pieces_per_hour": 100}).insert().name

	def matrix(op, rate, sam, slabs=None, urgent=0):
		doc = frappe.get_doc({
			"doctype": "Piece Rate Matrix", "company": company, "operation": op, "valid_from": add_days(ctx.day, -30),
			"standard_rate": rate, "overtime_multiplier": 1.5, "sam_minutes": sam, "urgent_premium_percent": urgent,
			"enable_slabs": 1 if slabs else 0, "slabs": slabs or [], "change_reason": "E2E",
		}).insert()
		doc.submit()
		return doc.name

	ctx.M_STITCH = matrix(ctx.STITCH, 2.0, 0.5, [{"from_qty": 0, "to_qty": 600, "rate_multiplier": 1.0},
												 {"from_qty": 600, "to_qty": 0, "rate_multiplier": 1.25}], urgent=10)
	ctx.M_CUT = matrix(ctx.CUT, 1.0, 0.25)
	ctx.M_PACK = matrix(ctx.PACK, 0.5, 0.2)

	ctx.CELL = frappe.get_doc({
		"doctype": "Production Cell", "cell_name": f"APW Cell {ctx.tag}", "company": company, "workstation": ctx.WS,
		"split_method": "By Hours Worked",
		"members": [{"employee": ctx.B, "skill_weight": 1, "is_active": 1}, {"employee": ctx.C, "skill_weight": 1, "is_active": 1}],
	}).insert().name

	def user(prefix, roles):
		email = f"{prefix}.{ctx.tag.lower()}@example.com"
		u = frappe.get_doc({"doctype": "User", "email": email, "first_name": prefix, "send_welcome_email": 0,
							"roles": [{"role": r} for r in roles]})
		u.flags.no_welcome_mail = True
		u.insert(ignore_permissions=True)
		return email

	ctx.U_SUP = user("apwsup", ["PieceWork Supervisor", "Employee"])
	ctx.U_MGR = user("apwmgr", ["PieceWork Manager", "Employee"])
	frappe.db.commit()


def t(ctx, hhmm):
	return f"{ctx.day} {hhmm}:00"


def log(ctx, submit=True, **kw):
	values = {"doctype": "Piece Rate Log", "company": ctx.company, "posting_date": ctx.day, "worker_type": "Individual",
			  "workstation": ctx.WS, "operation": ctx.STITCH}
	values.update(kw)
	doc = frappe.get_doc(values).insert()
	if submit:
		doc.submit()
	return doc


# ------------------------------------------------------------------ scenarios
def scenario_logs(ctx):
	print("\n[1] Piece Rate Logs - rates, capacity, overtime, overlap")
	l1 = log(ctx, employee=ctx.A, from_time=t(ctx, "08:00"), to_time=t(ctx, "12:00"), qty_logged=380)
	check("rate resolved from matrix", l1.piece_rate_matrix == ctx.M_STITCH and flt(l1.effective_rate) == 2.0)
	check("hours derived from timestamps", flt(l1.hours_worked) == 4.0, l1.hours_worked)
	check("capacity ceiling = stricter basis (rated 400)", flt(l1.capacity_limit) == 400 and l1.capacity_status == "Within Capacity",
		  f"{l1.capacity_limit} {l1.capacity_basis}")
	check("gross 380 x 2.00 = 760", flt(l1.gross_amount) == 760, l1.gross_amount)
	check("no review required", l1.review_status == "Not Required", l1.review_status)
	ctx.L1 = l1.name

	l2 = log(ctx, employee=ctx.A, from_time=t(ctx, "13:00"), to_time=t(ctx, "17:00"), overtime_hours=1, qty_logged=400)
	check("overtime apportioned: 300x2 + 100x2x1.5 = 900", flt(l2.gross_amount) == 900, l2.gross_amount)
	ctx.L2 = l2.name

	expect_error("overlapping window rejected",
				 lambda: log(ctx, submit=False, employee=ctx.A, from_time=t(ctx, "11:00"), to_time=t(ctx, "14:00"), qty_logged=10),
				 "overlap")
	expect_error("future to_time rejected",
				 lambda: log(ctx, submit=False, employee=ctx.A, from_time=f"{today()} 23:00:00",
							 to_time=f"{add_days(today(), 1)} 02:00:00", qty_logged=10), "future")
	expect_error("missing time window rejected",
				 lambda: log(ctx, submit=False, employee=ctx.A, qty_logged=10), "required")

	frappe.set_user(ctx.U_SUP)
	l4 = log(ctx, employee=ctx.A, from_time=t(ctx, "18:00"), to_time=t(ctx, "19:00"), qty_logged=150)
	frappe.set_user("Administrator")
	check("over-capacity log held for review (150 > 100)", l4.review_status == "Pending Review" and l4.docstatus == 1,
		  f"{l4.review_status} {l4.capacity_limit}")
	ctx.L4 = l4.name

	from alphax_piecework.alphax_piecework.doctype.piece_rate_log.piece_rate_log import review_log

	frappe.set_user(ctx.U_SUP)
	expect_error("supervisor cannot approve", lambda: review_log(l4.name, "Approved", "ok"), "not permitted")
	frappe.set_user(ctx.U_MGR)
	mine = log(ctx, employee=ctx.F, from_time=t(ctx, "05:00"), to_time=t(ctx, "06:00"), qty_logged=150)
	expect_error("manager cannot approve own log (SoD)", lambda: review_log(mine.name, "Approved", "self"), "segregation")
	check("manager approves someone else's log", review_log(l4.name, "Approved", "Line trial, verified on camera") == "Approved")
	frappe.set_user("Administrator")
	mine.reload()
	mine.cancel()
	check("pending log cancelled cleanly", mine.docstatus == 2)

	urgent = log(ctx, submit=False, employee=ctx.F, from_time=t(ctx, "07:00"), to_time=t(ctx, "08:00"), qty_logged=50, is_urgent=1)
	check("urgent premium 10%: 50x2x1.10 = 110", flt(urgent.gross_amount) == 110, urgent.gross_amount)
	urgent.delete()


def scenario_qc(ctx):
	print("\n[2] Shop Floor QA - responsibility routing")
	qa1 = frappe.get_doc({"doctype": "Shop Floor QA Log", "piece_rate_log": ctx.L1, "inspection_date": ctx.day,
						  "qty_inspected": 380, "qty_failed": 20, "defect_code": "WM-DIM", "rework_employee": ctx.D}).insert()
	check("defect code sets responsibility + disposition", qa1.responsibility == "Worker" and qa1.disposition == "Rework - Third Party",
		  f"{qa1.responsibility}/{qa1.disposition}")
	check("worker fault: deduct 20 x 2.00 = 40, specialist earns 40",
		  flt(qa1.original_deduction) == 40 and flt(qa1.rework_payout) == 40, f"{qa1.original_deduction}/{qa1.rework_payout}")
	qa1.submit()

	qa2 = frappe.get_doc({"doctype": "Shop Floor QA Log", "piece_rate_log": ctx.L2, "inspection_date": ctx.day,
						  "qty_failed": 10, "defect_code": "MC-FAULT", "disposition": "Scrap"}).insert()
	check("machine fault: worker never charged", flt(qa2.total_worker_deduction) == 0, qa2.total_worker_deduction)
	qa2.submit()

	expect_error("rework specialist must differ from worker",
				 lambda: frappe.get_doc({"doctype": "Shop Floor QA Log", "piece_rate_log": ctx.L1, "inspection_date": ctx.day,
										 "qty_failed": 1, "defect_code": "WM-DIM", "rework_employee": ctx.A}).insert(), "differ")
	expect_error("over-rejection blocked",
				 lambda: frappe.get_doc({"doctype": "Shop Floor QA Log", "piece_rate_log": ctx.L1, "inspection_date": ctx.day,
										 "qty_failed": 361, "defect_code": "MC-FAULT", "disposition": "Scrap"}).insert(), "exceed")

	l1 = frappe.get_doc("Piece Rate Log", ctx.L1)
	check("log rolled up QC: accepted 360, net 720", flt(l1.qty_accepted) == 360 and flt(l1.net_amount) == 720,
		  f"{l1.qty_accepted}/{l1.net_amount}")
	expect_error("cannot cancel log with open QA", lambda: frappe.get_doc("Piece Rate Log", ctx.L2).cancel(), "qa")
	ctx.QA1, ctx.QA2 = qa1.name, qa2.name


def scenario_cell(ctx):
	print("\n[3] Production Cell split")
	l5 = log(ctx, worker_type="Production Cell", production_cell=ctx.CELL, from_time=t(ctx, "08:00"), to_time=t(ctx, "12:00"),
			 qty_logged=700)
	check("cell capacity scales with head-count (800)", flt(l5.capacity_limit) == 800 and l5.capacity_status == "Within Capacity",
		  l5.capacity_limit)
	amounts = sorted(flt(s.amount) for s in l5.splits)
	check("cell 1400 split 700/700", amounts == [700, 700] and flt(sum(amounts)) == flt(l5.gross_amount), amounts)
	expect_error("cell member cannot double-log the same window",
				 lambda: log(ctx, submit=False, employee=ctx.B, from_time=t(ctx, "09:00"), to_time=t(ctx, "10:00"), qty_logged=10),
				 "overlap")
	ctx.L5 = l5.name


def make_work_order(ctx, with_routing=True):
	company, abbr = ctx.company, ctx.abbr
	if not ctx.get("BOM"):
		fg = frappe.get_doc({"doctype": "Item", "item_code": f"APW-SHIRT-{ctx.tag}", "item_group": "Products", "stock_uom": "Nos",
							 "is_stock_item": 1}).insert().name
		rm = frappe.get_doc({"doctype": "Item", "item_code": f"APW-FABRIC-{ctx.tag}", "item_group": "Raw Material",
							 "stock_uom": "Nos", "is_stock_item": 1, "valuation_rate": 5}).insert().name
		bom = frappe.get_doc({
			"doctype": "BOM", "item": fg, "company": company, "quantity": 1, "with_operations": 1,
			"rm_cost_as_per": "Valuation Rate", "items": [{"item_code": rm, "qty": 1, "rate": 5}],
			"operations": [{"operation": ctx.CUT, "workstation": ctx.WS, "time_in_mins": 1},
						   {"operation": ctx.STITCH, "workstation": ctx.WS, "time_in_mins": 2}],
		}).insert()
		bom.submit()
		ctx.BOM, ctx.FG = bom.name, fg
	wo = frappe.get_doc({
		"doctype": "Work Order", "production_item": ctx.FG, "bom_no": ctx.BOM, "qty": 100, "company": company,
		"wip_warehouse": f"Work In Progress - {abbr}", "fg_warehouse": f"Finished Goods - {abbr}",
		"source_warehouse": f"Stores - {abbr}", "skip_transfer": 1, "planned_start_date": now_datetime(),
	})
	if with_routing:
		wo.set_work_order_operations()  # what the desk form does
	wo.insert()
	wo.submit()
	return wo.name, ctx.FG


def scenario_work_order(ctx):
	print("\n[4] Work Order routing, flow balance, material balance")
	try:
		ctx.WO, ctx.FG = make_work_order(ctx)
		ctx.WO_NOROUTE, _ = make_work_order(ctx, with_routing=False)
	except Exception:
		check("work order fixture", False, traceback.format_exc()[-1200:])
		return
	wo = dict(employee=ctx.E, work_order=ctx.WO)
	expect_error("STITCH before CUT blocked (flow balance)",
				 lambda: log(ctx, operation=ctx.STITCH, from_time=t(ctx, "09:00"), to_time=t(ctx, "10:00"), qty_logged=50, **wo),
				 "previous operation")
	cut = log(ctx, operation=ctx.CUT, from_time=t(ctx, "06:00"), to_time=t(ctx, "07:00"), qty_logged=90, **wo)
	check("CUT 90 accepted; item fetched from WO", cut.docstatus == 1 and cut.item_code == ctx.FG, cut.item_code)
	expect_error("STITCH 95 > CUT 90 blocked",
				 lambda: log(ctx, operation=ctx.STITCH, from_time=t(ctx, "09:00"), to_time=t(ctx, "10:00"), qty_logged=95, **wo),
				 "previous operation")
	st = log(ctx, operation=ctx.STITCH, from_time=t(ctx, "09:00"), to_time=t(ctx, "10:00"), qty_logged=90, **wo)
	check("STITCH 90 accepted", st.docstatus == 1)
	expect_error("CUT total 110 > WO qty 100 blocked (material balance)",
				 lambda: log(ctx, operation=ctx.CUT, from_time=t(ctx, "10:00"), to_time=t(ctx, "11:00"), qty_logged=20, **wo),
				 "limit")
	expect_error("routing falls back to BOM when WO has no operations table",
				 lambda: log(ctx, employee=ctx.F, work_order=ctx.WO_NOROUTE, operation=ctx.PACK, from_time=t(ctx, "20:00"),
							 to_time=t(ctx, "21:00"), qty_logged=10), "routing")
	expect_error("operation outside routing blocked",
				 lambda: log(ctx, operation=ctx.PACK, from_time=t(ctx, "11:00"), to_time=t(ctx, "12:00"), qty_logged=10, **wo),
				 "routing")


def scenario_batch(ctx):
	print("\n[5] Wage batch - slabs, deductions, JE posting, stale guard, reversal")

	def new_batch():
		b = frappe.get_doc({"doctype": "PieceWork Wage Batch", "company": ctx.company, "posting_date": today(),
							"from_date": ctx.day, "to_date": ctx.day, "department": ctx.dept,
							"posting_mode": "Journal Entry Accrual"}).insert()
		b.fetch_entries()
		b.reload()
		return b

	b1 = new_batch()
	rows = {r.employee: r for r in b1.employees}
	a = rows.get(ctx.A)
	# A: gross 760+900+300=1960; slab on ACCEPTED 360+390+150=900 -> (900-600)x2x0.25=150; QC 40 -> net 2070
	check("worker A gross 1960 (approved over-capacity log included)", a and flt(a.gross_earnings) == 1960, a and a.gross_earnings)
	check("worker A slab on accepted pieces = 150", a and flt(a.slab_incentive) == 150, a and a.slab_incentive)
	check("worker A QC deduction 40, net 2070", a and flt(a.qc_deductions) == 40 and flt(a.net_payable) == 2070,
		  a and f"{a.qc_deductions}/{a.net_payable}")
	d = rows.get(ctx.D)
	check("rework specialist D paid 40", d and flt(d.rework_earnings) == 40 and flt(d.net_payable) == 40, d and d.net_payable)
	b, c = rows.get(ctx.B), rows.get(ctx.C)
	check("cell members B & C: 700 + slab 25 each", b and c and flt(b.net_payable) == 725 and flt(c.net_payable) == 725,
		  f"{b and b.net_payable}/{c and c.net_payable}")
	e = rows.get(ctx.E)
	check("WO worker E: 90x1 + 90x2 = 270", e and flt(e.net_payable) == 270, e and e.net_payable)
	check("batch total net 3830", flt(b1.total_net) == 3830, b1.total_net)

	b2 = new_batch()
	b1.submit()
	b1.reload()
	check("batch posted inline", b1.status == "Posted" and b1.journal_entry, f"{b1.status} {b1.posting_error}")
	if b1.journal_entry:
		je = frappe.get_doc("Journal Entry", b1.journal_entry)
		check("JE submitted and balanced at 3830", je.docstatus == 1 and flt(je.total_debit) == 3830 and flt(je.total_credit) == 3830,
			  f"{je.total_debit}/{je.total_credit}")
	check("source logs marked Posted", frappe.db.get_value("Piece Rate Log", ctx.L1, "payroll_status") == "Posted")
	check("QA logs marked Posted", frappe.db.get_value("Shop Floor QA Log", ctx.QA1, "payroll_status") == "Posted")
	expect_error("stale second batch cannot double-pay", lambda: frappe.get_doc("PieceWork Wage Batch", b2.name).submit(), "changed since entries were fetched")
	expect_error("cannot cancel a paid log", lambda: frappe.get_doc("Piece Rate Log", ctx.L5).cancel(), "batch")

	b1 = frappe.get_doc("PieceWork Wage Batch", b1.name)
	b1.cancel()
	check("batch cancel reverses JE", frappe.db.get_value("Journal Entry", b1.journal_entry, "docstatus") == 2)
	check("batch cancel releases logs", frappe.db.get_value("Piece Rate Log", ctx.L1, ["payroll_status", "wage_batch"]) == ("Unbatched", None))

	b3 = new_batch()
	check("re-fetch after cancel gives same total", flt(b3.total_net) == 3830, b3.total_net)
	ctx.B3 = b3.name
	frappe.get_doc("PieceWork Wage Batch", b2.name).delete()


def scenario_hrms(ctx):
	if "hrms" not in frappe.get_installed_apps():
		print("\n[6] HRMS not installed - Additional Salary path skipped")
		return
	print("\n[6] HRMS Additional Salary posting")
	comp = f"APW Piece Wages {ctx.tag}"
	frappe.get_doc({"doctype": "Salary Component", "salary_component": comp, "salary_component_abbr": f"PW{ctx.tag[:3]}",
					"type": "Earning", "accounts": [{"company": ctx.company, "account": f"Salary - {ctx.abbr}"}]}).insert()
	settings = frappe.get_doc("PieceWork Settings")
	for r in settings.company_accounts:
		if r.company == ctx.company:
			r.salary_component = comp
	settings.save()
	b = frappe.get_doc("PieceWork Wage Batch", ctx.B3)
	b.posting_mode = "Additional Salary (HRMS)"
	b.save()
	expect_error("pre-flight lists employees without salary structure",
				 lambda: frappe.get_doc("PieceWork Wage Batch", ctx.B3).submit(), "no salary structure assignment")

	basic = f"APW Basic {ctx.tag}"
	frappe.get_doc({"doctype": "Salary Component", "salary_component": basic, "salary_component_abbr": f"B{ctx.tag[:3]}",
					"type": "Earning", "accounts": [{"company": ctx.company, "account": f"Salary - {ctx.abbr}"}]}).insert()
	ss = frappe.get_doc({"doctype": "Salary Structure", "name": f"APW Structure {ctx.tag}", "company": ctx.company,
						 "payroll_frequency": "Monthly", "currency": frappe.get_cached_value("Company", ctx.company, "default_currency"),
						 "is_active": "Yes", "earnings": [{"salary_component": basic, "amount": 1000}]}).insert()
	ss.submit()
	frappe.db.set_value("Company", ctx.company, "default_payroll_payable_account", f"Payroll Payable - {ctx.abbr}")
	for row in b.employees:
		frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": row.employee, "salary_structure": ss.name,
						"company": ctx.company, "from_date": add_days(ctx.day, -20), "base": 1000,
						"currency": ss.currency, "payroll_payable_account": f"Payroll Payable - {ctx.abbr}"}).insert().submit()
	b = frappe.get_doc("PieceWork Wage Batch", ctx.B3)
	b.submit()
	b.reload()
	made = [r.additional_salary for r in b.employees if r.additional_salary]
	check("Additional Salary per payable employee", b.status in ("Posted", "Partially Posted") and len(made) == len(b.employees),
		  f"{b.status} {len(made)}/{len(b.employees)} {(b.posting_error or '')[-300:]}")
	if made:
		amt = frappe.db.get_value("Additional Salary", made[0], ["amount", "docstatus", "ref_docname"], as_dict=True)
		check("Additional Salary submitted & referenced", amt.docstatus == 1 and amt.ref_docname == b.name)
	b.cancel()
	check("cancel reverses Additional Salaries", all(frappe.db.get_value("Additional Salary", n, "docstatus") == 2 for n in made))


def scenario_job_card(ctx):
	print("\n[6b] Job Card bridge")
	if not ctx.get("BOM"):
		check("job card scenario needs the BOM fixture", False)
		return
	settings = frappe.get_doc("PieceWork Settings")
	settings.enable_job_card_bridge = 1
	settings.save()
	wo_name, _ = make_work_order(ctx)
	jc_name = frappe.db.get_value("Job Card", {"work_order": wo_name, "operation": ctx.CUT}, "name")
	check("ERPNext created job cards from routing", bool(jc_name))
	if not jc_name:
		return
	jc = frappe.get_doc("Job Card", jc_name)
	jc.append("time_logs", {"employee": ctx.E, "from_time": t(ctx, "12:00"), "to_time": t(ctx, "13:00"), "completed_qty": 100,
							"time_in_mins": 60})
	jc.save()
	jc.submit()
	logs = frappe.get_all("Piece Rate Log", filters={"job_card": jc.name}, fields=["name", "docstatus", "qty_logged", "gross_amount", "source"])
	check("submitted Job Card produced a submitted Piece Rate Log (100 x 1.00)",
		  len(logs) == 1 and logs[0].docstatus == 1 and flt(logs[0].gross_amount) == 100 and logs[0].source == "Job Card", logs)
	jc.reload()
	jc.cancel()
	check("cancelling the Job Card cancels its log",
		  all(frappe.db.get_value("Piece Rate Log", l.name, "docstatus") == 2 for l in logs))
	settings.reload()
	settings.enable_job_card_bridge = 0
	settings.save()


def scenario_background_posting(ctx):
	print("\n[6c] Background posting - failure isolation and retry")
	from alphax_piecework.engine import payroll

	settings = frappe.get_doc("PieceWork Settings")
	settings.enqueue_threshold = 1
	acc_row = next(r for r in settings.company_accounts if r.company == ctx.company)
	good_expense = acc_row.wage_expense_account
	acc_row.wage_expense_account = frappe.db.get_value("Account", {"company": ctx.company, "is_group": 1, "root_type": "Expense"}, "name")
	settings.save()
	try:
		b = frappe.get_doc({"doctype": "PieceWork Wage Batch", "company": ctx.company, "posting_date": today(), "from_date": ctx.day,
							"to_date": ctx.day, "department": ctx.dept, "posting_mode": "Journal Entry Accrual"}).insert()
		b.fetch_entries()
		b = frappe.get_doc("PieceWork Wage Batch", b.name)
		b.submit()
		check("large batch is queued, not posted inline", frappe.db.get_value("PieceWork Wage Batch", b.name, "status") == "Queued")
		frappe.db.commit()
		status = payroll.post_batch(b.name, in_background=True)  # what the worker runs
		row = frappe.db.get_value("PieceWork Wage Batch", b.name, ["status", "posting_error", "journal_entry", "docstatus"], as_dict=True)
		check("posting failure flags batch Failed, keeps it submitted, no JE",
			  status == "Failed" and row.status == "Failed" and row.docstatus == 1 and not row.journal_entry and row.posting_error,
			  f"{status} {row.status} {row.journal_entry}")
		check("sources stay Batched (no double pay) while failed",
			  frappe.db.get_value("Piece Rate Log", ctx.L1, "payroll_status") == "Batched")
		settings.reload()
		next(r for r in settings.company_accounts if r.company == ctx.company).wage_expense_account = good_expense
		settings.save()
		b = frappe.get_doc("PieceWork Wage Batch", b.name)
		check("retry re-queues", b.retry_posting() == "Queued")
		frappe.db.commit()
		status = payroll.post_batch(b.name, in_background=True)
		row = frappe.db.get_value("PieceWork Wage Batch", b.name, ["status", "journal_entry"], as_dict=True)
		check("retry posts JE after fix", status == "Posted" and row.journal_entry and
			  frappe.db.get_value("Journal Entry", row.journal_entry, "docstatus") == 1, f"{status} {row}")
		check("sources Posted after retry", frappe.db.get_value("Piece Rate Log", ctx.L1, "payroll_status") == "Posted")
	finally:
		settings.reload()
		settings.enqueue_threshold = 50
		r = next(r for r in settings.company_accounts if r.company == ctx.company)
		r.wage_expense_account = good_expense
		settings.save()
		frappe.db.commit()


def scenario_audit(ctx):
	print("\n[7] Tamper-evident audit trail")
	from alphax_piecework.engine.audit import verify_chain

	res = verify_chain()
	ok = res.get("ok") if isinstance(res, dict) else bool(res)
	check("hash chain verifies", ok, res)
	last = frappe.get_all("PieceWork Audit Event", order_by="sequence desc", limit=1, pluck="name")
	if last:
		frappe.db.set_value("PieceWork Audit Event", last[0], "reason", "tampered", update_modified=False)
		res2 = verify_chain()
		bad = (res2.get("ok") is False) if isinstance(res2, dict) else not res2
		check("direct DB tampering detected", bad, res2)
		frappe.db.rollback()


def scenario_edge(ctx):
	print("\n[8] Edge ingest - dedup, validation, aggregation")
	from alphax_piecework.api.edge import handshake, ingest
	from alphax_piecework.engine.edge import process_queued_events

	expect_error("Administrator cannot be a device user",
				 lambda: frappe.get_doc({"doctype": "PieceWork Edge Device", "device_id": f"BAD-{ctx.tag}", "company": ctx.company,
										 "api_user": "Administrator", "is_active": 1}).insert(), "dedicated")
	dev_user = frappe.get_doc({"doctype": "User", "email": f"apwdev.{ctx.tag.lower()}@example.com", "first_name": "Device",
							   "user_type": "System User", "send_welcome_email": 0, "roles": [{"role": "PieceWork Device"}]})
	dev_user.insert(ignore_permissions=True)
	dev = frappe.get_doc({"doctype": "PieceWork Edge Device", "device_id": f"TAB-{ctx.tag}", "device_name": "Line tablet",
						  "company": ctx.company, "api_user": dev_user.name, "workstation": ctx.WS,
						  "default_operation": ctx.PACK, "is_active": 1}).insert()
	frappe.set_user(ctx.U_SUP)
	expect_error("other users cannot post as the device", lambda: ingest(dev.name, "[]"), "not bound")
	frappe.set_user(dev_user.name)
	check("handshake returns server time", bool(handshake(dev.name).get("server_time")))
	u1, u2 = f"{ctx.tag}-e1", f"{ctx.tag}-e2"
	events = [
		{"client_uuid": u1, "employee": ctx.F, "qty": 40, "from_time": t(ctx, "14:00"), "to_time": t(ctx, "14:30")},
		{"client_uuid": u2, "employee": ctx.F, "qty": 35, "from_time": t(ctx, "14:30"), "to_time": t(ctx, "15:00")},
		{"client_uuid": u1, "employee": ctx.F, "qty": 40},
		{"client_uuid": f"{ctx.tag}-bad", "employee": ctx.F, "qty": 0},
	]
	r1 = ingest(dev.name, json.dumps(events))
	check("2 accepted, in-request duplicate caught, bad qty rejected",
		  sorted(r1["accepted"]) == sorted([u1, u2]) and r1["duplicates"] == [u1] and len(r1["rejected"]) == 1, r1)
	r2 = ingest(dev.name, json.dumps(events[:2]))
	check("replay after Wi-Fi drop = all duplicates", sorted(r2["duplicates"]) == sorted([u1, u2]) and not r2["accepted"], r2)
	frappe.set_user("Administrator")
	process_queued_events()
	names = frappe.get_all("PieceWork Edge Event", filters={"client_uuid": ["in", [u1, u2]]}, fields=["status", "piece_rate_log", "error"])
	logs = {n.piece_rate_log for n in names}
	check("both events processed into one draft log", all(n.status == "Processed" for n in names) and len(logs) == 1, names)
	if len(logs) == 1:
		lg = frappe.get_doc("Piece Rate Log", logs.pop())
		check("draft log aggregates 75 pcs over 14:00-15:00",
			  flt(lg.qty_logged) == 75 and flt(lg.hours_worked) == 1.0 and lg.docstatus == 0, f"{lg.qty_logged} {lg.hours_worked}")


def scenario_reports(ctx):
	print("\n[9] Reports, leaderboard, print format, workspace")
	from frappe.desk.query_report import run

	filters = {"company": ctx.company, "from_date": ctx.day, "to_date": ctx.day}
	for name, extra in (("Piece Rate Earnings Register", {}), ("Labor Effectiveness", {"group_by": "Employee"}),
						("Labor Effectiveness", {"group_by": "Workstation"}), ("Defect Pareto", {}), ("Capacity Exceptions", {})):
		try:
			res = run(name, filters={**filters, **extra})
			check(f"report '{name}' {extra or ''} runs ({len(res.get('result') or [])} rows)", res.get("result") is not None)
		except Exception as e:
			check(f"report '{name}' runs", False, str(e)[:200])
	reg = run("Piece Rate Earnings Register", filters={**filters, "employee": ctx.B})
	rows = [r for r in reg["result"] if isinstance(r, dict) and r.get("log") == ctx.L5]
	check("register explodes cell log per member", len(rows) == 1 and flt(rows[0]["net"]) == 700, rows)
	par = run("Defect Pareto", filters=filters)["result"]
	check("pareto has cumulative %", any(isinstance(r, dict) and r.get("cumulative") for r in par))

	from alphax_piecework.api.dashboard import get_leaderboard

	lb = get_leaderboard(company=ctx.company, date=ctx.day)
	check("leaderboard returns board + totals, no money fields",
		  "board" in lb and "totals" in lb and not any(k in json.dumps(lb, default=str) for k in ("net_amount", "gross_amount")),
		  list(lb.keys()))
	pf_html = frappe.db.get_value("Print Format", "PieceWork Wage Statement", "html")
	html = frappe.render_template(pf_html, {"doc": frappe.get_doc("PieceWork Wage Batch", ctx.B3)})
	check("bilingual wage statement renders", "كشف أجور" in html and "Piece-Rate Wage Statement" in html)
	check("workspace installed", frappe.db.exists("Workspace", "AlphaX PieceWork"))
	from frappe.desk.desk_page import get as get_page

	for page in ("shopfloor-leaderboard", "piecework-terminal"):
		pdoc = get_page(page)
		check(f"desk page '{page}' loads with script + style", bool(pdoc.get("script")) and bool(pdoc.get("style")))
	check("custom fields installed", frappe.db.exists("Custom Field", {"dt": "Workstation", "fieldname": "apw_max_pieces_per_hour"}))


def run():
	if not cint(frappe.conf.get("allow_tests")):
		raise frappe.ValidationError("Refusing to run: set allow_tests in site_config.json (test/staging sites only).")
	RESULTS.clear()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	ctx = Ctx()
	print(f"AlphaX PieceWork E2E on {frappe.local.site}")
	setup(ctx)
	print(f"tag={ctx.tag} company={ctx.company} day={ctx.day}")
	for step in (scenario_logs, scenario_qc, scenario_cell, scenario_work_order, scenario_batch, scenario_hrms, scenario_job_card, scenario_background_posting,
				 scenario_audit, scenario_edge, scenario_reports):
		try:
			step(ctx)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.set_user("Administrator")
			check(f"{step.__name__} crashed", False, traceback.format_exc()[-1500:])
	passed = sum(1 for ok, *_ in RESULTS if ok)
	print(f"\nRESULT: {passed}/{len(RESULTS)} passed")
	for ok, name, detail in RESULTS:
		if not ok:
			print(f"FAILED: {name}\n{detail}\n")
	return {"passed": passed, "total": len(RESULTS)}
