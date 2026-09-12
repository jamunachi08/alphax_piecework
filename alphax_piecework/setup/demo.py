"""Demo dataset for AlphaX PieceWork.

Seeds a small garment plant and drives it through the whole flow twice, so the
Process Flow board shows a finished period beside a live one:

    Week 1-2   closed period   - captured, inspected, reviewed, batched and posted
    Week 3     open period     - logs captured, one capacity hold still awaiting a
                                 manager, QC recorded, nothing batched yet

Run it on a demo or staging site:

    bench --site <site> execute alphax_piecework.setup.demo.run

`run(reset_first=True)` removes what a previous run created before seeding again.
It refuses to run on a site that already holds posted wage batches unless
`force=True`, so it cannot quietly disturb real payroll data.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate, today

TAG = "APW-DEMO"

WORKERS = [
	("Imran", "Hussain", "Expert", 110),
	("Bilal", "Ahmed", "Qualified", 100),
	("Chandran", "Nair", "Qualified", 100),
	("Dawood", "Khan", "Expert", 115),
	("Emmanuel", "Osei", "Qualified", 100),
	("Faisal", "Al Qahtani", "Trainee", 80),
	("Ganesh", "Iyer", "Qualified", 100),
	("Hamza", "Rahman", "Trainee", 85),
]
OPERATIONS = [
	("Cutting", 0.30, 1.20, 140),
	("Stitching", 0.60, 2.40, 95),
	("Finishing", 0.35, 1.40, 130),
	("Packing", 0.20, 0.80, 200),
]
DOCTYPES_IN_ORDER = [
	"PieceWork Wage Batch", "Shop Floor QA Log", "Piece Rate Log", "Piece Rate Matrix",
	"Employee Operation Skill", "Production Cell",
]


def log(msg):
	print(f"  {msg}")


def d(offset):
	return add_days(nowdate(), offset)


def t(day_offset, hhmm):
	return f"{d(day_offset)} {hhmm}:00"


# ---------------------------------------------------------------- masters
def ensure_company():
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {}, "name")
	if not company:
		frappe.throw(_("Create a Company before seeding demo data."))
	return company


def ensure_department(company):
	name = f"Production - {frappe.get_cached_value('Company', company, 'abbr')}"
	if frappe.db.exists("Department", name):
		return name
	doc = frappe.get_doc({"doctype": "Department", "department_name": "Production", "company": company})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def ensure_workers(company, department):
	"""Employees are reused between runs; the demo never deletes staff records."""
	out = []
	for first, last, level, factor in WORKERS:
		existing = frappe.db.get_value("Employee", {"first_name": first, "last_name": last,
													"company": company}, "name")
		if existing:
			out.append((existing, level, factor))
			continue
		emp = frappe.get_doc({
			"doctype": "Employee", "first_name": first, "last_name": last, "gender": "Male",
			"date_of_birth": "1992-03-15", "date_of_joining": "2023-01-10", "company": company,
			"status": "Active", "department": department, "apw_piece_rate_eligible": 1,
		})
		emp.flags.ignore_permissions = True
		emp.insert()
		out.append((emp.name, level, factor))
	return out


def ensure_operations():
	out = []
	for name, sam, rate, per_hour in OPERATIONS:
		op_name = f"APW {name}"
		if not frappe.db.exists("Operation", op_name):
			doc = frappe.get_doc({"doctype": "Operation", "name": op_name, "apw_default_sam_minutes": sam})
			doc.flags.ignore_permissions = True
			doc.insert()
		else:
			frappe.db.set_value("Operation", op_name, "apw_default_sam_minutes", sam)
		out.append((op_name, sam, rate, per_hour))
	return out


def ensure_workstation(per_hour=160):
	name = "APW Line 1"
	if not frappe.db.exists("Workstation", name):
		doc = frappe.get_doc({"doctype": "Workstation", "workstation_name": name,
							  "apw_max_pieces_per_hour": per_hour})
		doc.flags.ignore_permissions = True
		doc.insert()
	else:
		frappe.db.set_value("Workstation", name, "apw_max_pieces_per_hour", per_hour)
	return name


def ensure_accounts(company):
	settings = frappe.get_doc("PieceWork Settings")
	abbr = frappe.get_cached_value("Company", company, "abbr")
	if any(r.company == company and r.wage_expense_account for r in settings.company_accounts):
		return settings
	expense = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense",
											  "account_name": ["like", "%Salary%"]}, "name") \
		or frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense"}, "name")
	payable = frappe.db.get_value("Account", {"company": company, "is_group": 0,
											  "account_type": "Payable"}, "name") \
		or frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Liability"}, "name")
	cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
	row = next((r for r in settings.company_accounts if r.company == company), None)
	if not row:
		row = settings.append("company_accounts", {"company": company})
	row.wage_expense_account = expense
	row.wage_payable_account = payable
	row.default_cost_center = cost_center
	settings.flags.ignore_permissions = True
	settings.save()
	return settings


def configure_settings(company):
	settings = ensure_accounts(company)
	settings.capacity_action = "Hold for Review"
	settings.capacity_basis = "Stricter of Both"
	settings.max_efficiency_percent = 130
	settings.posting_mode = "Journal Entry Accrual"
	settings.require_time_window = 1
	settings.block_overlapping_logs = 1
	settings.enable_deduction_caps = 1
	settings.enable_material_balance = 0
	settings.enable_flow_balance = 0
	settings.enable_job_card_bridge = 0
	settings.flags.ignore_permissions = True
	settings.save()
	return settings


# ---------------------------------------------------------------- stages
def seed_rates(company, operations):
	"""Stage 1 — an effective-dated rate per operation, with slabs on the main one."""
	matrices = {}
	for op_name, sam, rate, _per_hour in operations:
		existing = frappe.db.get_value("Piece Rate Matrix",
									   {"operation": op_name, "company": company, "docstatus": 1}, "name")
		if existing:
			matrices[op_name] = existing
			continue
		values = {
			"doctype": "Piece Rate Matrix", "company": company, "operation": op_name,
			"valid_from": d(-60), "standard_rate": rate, "overtime_multiplier": 1.5,
			"sam_minutes": sam, "urgent_premium_percent": 10, "multi_skill_bonus_percent": 5,
			"change_reason": f"[{TAG}] Annual rate review",
		}
		if op_name.endswith("Stitching"):
			values.update({"enable_slabs": 1, "slabs": [
				{"from_qty": 0, "to_qty": 500, "rate_multiplier": 1.0},
				{"from_qty": 500, "to_qty": 800, "rate_multiplier": 1.15},
				{"from_qty": 800, "to_qty": 0, "rate_multiplier": 1.3},
			]})
		doc = frappe.get_doc(values)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		matrices[op_name] = doc.name
	log(f"stage 1: {len(matrices)} rate matrices")
	return matrices


def seed_skills(workers, operations, company, workstation):
	"""Stage 2 — certified skills and one production cell."""
	made = 0
	for employee, level, factor in workers:
		for op_name, *_rest in operations:
			if frappe.db.exists("Employee Operation Skill", {"employee": employee, "operation": op_name}):
				continue
			doc = frappe.get_doc({
				"doctype": "Employee Operation Skill", "employee": employee, "operation": op_name,
				"is_active": 1, "skill_level": level, "capacity_factor_percent": factor,
				"certified_on": d(-90), "certified_by": frappe.session.user,
			})
			doc.flags.ignore_permissions = True
			doc.insert()
			made += 1
	cell = frappe.db.get_value("Production Cell", {"cell_name": "APW Finishing Cell"}, "name")
	if not cell:
		doc = frappe.get_doc({
			"doctype": "Production Cell", "cell_name": "APW Finishing Cell", "company": company,
			"workstation": workstation, "split_method": "By Hours Worked", "is_active": 1,
			"supervisor": workers[0][0],
			# cell members are deliberately outside workers[:5], who log individually on the same shifts
			"members": [{"employee": workers[i][0], "skill_weight": 1, "is_active": 1} for i in (5, 6, 7)],
		})
		doc.flags.ignore_permissions = True
		doc.insert()
		cell = doc.name
	log(f"stage 2: {made} skill certificates, cell {cell}")
	return cell


def make_log(company, workstation, employee, operation, day, start, end, qty, overtime=0, urgent=0,
			 cell=None, submit=True):
	values = {
		"doctype": "Piece Rate Log", "company": company, "posting_date": d(day), "workstation": workstation,
		"operation": operation, "from_time": t(day, start), "to_time": t(day, end),
		"qty_logged": qty, "overtime_hours": overtime, "is_urgent": urgent,
	}
	if cell:
		values.update({"worker_type": "Production Cell", "production_cell": cell})
	else:
		values.update({"worker_type": "Individual", "employee": employee})
	doc = frappe.get_doc(values)
	doc.flags.ignore_permissions = True
	doc.insert()
	if submit:
		doc.submit()
	return doc


def seed_period(company, workstation, workers, operations, cell, days, urgent_day=None,
				hold_worker_index=None, approve_hold=True):
	"""Stages 3-5 — a run of shifts, including one log that exceeds capacity."""
	created, held = [], []
	ops = [op[0] for op in operations]
	shifts = [("08:00", "12:00"), ("13:00", "17:00")]
	for offset, day in enumerate(days):
		for w_index, (employee, level, _factor) in enumerate(workers[:5]):
			operation = ops[w_index % len(ops)]
			start, end = shifts[offset % 2]
			base = {"APW Cutting": 420, "APW Stitching": 300, "APW Finishing": 380, "APW Packing": 560}[operation]
			qty = base + (w_index * 7) + (offset * 5)
			overtime = 1 if (offset % 3 == 0 and w_index < 2) else 0
			urgent = 1 if (urgent_day is not None and day == urgent_day and w_index == 0) else 0
			if overtime:
				end = "18:00" if start == "13:00" else "13:00"
			created.append(make_log(company, workstation, employee, operation, day, start, end, qty,
									overtime=overtime, urgent=urgent))
		# the finishing cell works the same day
		created.append(make_log(company, workstation, None, "APW Finishing", day, "08:00", "12:00",
								900 + offset * 20, cell=cell))

	if hold_worker_index is not None:
		employee = workers[hold_worker_index][0]
		# one hour, far more pieces than the ceiling allows: this is held for review
		doc = make_log(company, workstation, employee, "APW Packing", days[-1], "18:00", "19:00", 340)
		held.append(doc)
		if approve_hold:
			from alphax_piecework.alphax_piecework.doctype.piece_rate_log.piece_rate_log import review_log

			review_log(doc.name, "Approved",
					   "Verified against the machine counter and the shift supervisor's log. Genuine short run.")
	return created, held


DEFECTS = [
	("WM-STITCH", "Worker", "Rework - Third Party", 18),
	("MC-FAULT", "Machine", "Scrap", 12),
	("WM-DIM", "Worker", "Scrap", 9),
	("MT-DEFECT", "Material Supplier", "Scrap", 14),
]


def seed_qc(company, logs, workers, day_hint=None):
	"""Stages 6-7 — QC inspections covering worker fault and not-worker fault."""
	made = []
	codes = {row[0]: row for row in DEFECTS}
	available = [c for c in codes if frappe.db.exists("PieceWork Defect Code", c)]
	if not available:
		available = frappe.get_all("PieceWork Defect Code", pluck="name", limit=4)
	rework_employee = workers[7][0]
	for i, source in enumerate(logs[:8]):
		source.reload()
		if flt(source.qty_logged) < 40:
			continue
		code = available[i % len(available)]
		defaults = frappe.db.get_value("PieceWork Defect Code", code,
									   ["responsibility", "default_disposition"], as_dict=True) or {}
		values = {
			"doctype": "Shop Floor QA Log", "piece_rate_log": source.name,
			"inspection_date": day_hint or source.posting_date,
			"qty_failed": 8 + i * 2, "defect_code": code,
			"evidence": f"[{TAG}] Sampled 40 pieces at the line-end check.",
		}
		if (defaults.get("default_disposition") or "").startswith("Rework - Third Party"):
			# the specialist must never be the worker who made the piece, or a member of the cell that did
			specialist = rework_employee
			if source.worker_type == "Production Cell" or source.employee == rework_employee:
				specialist = workers[0][0]
			if specialist == source.employee:
				specialist = workers[1][0]
			values["rework_employee"] = specialist
		doc = frappe.get_doc(values)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		made.append(doc)
	log(f"stages 6-7: {len(made)} QC inspections")
	return made


def seed_batch(company, from_day, to_day, department=None, post=True):
	"""Stages 8-9 — build the wage batch and post it."""
	batch = frappe.get_doc({
		"doctype": "PieceWork Wage Batch", "company": company, "posting_date": d(to_day + 1),
		"from_date": d(from_day), "to_date": d(to_day), "department": department,
		"posting_mode": "Journal Entry Accrual",
	})
	batch.flags.ignore_permissions = True
	batch.insert()
	batch.fetch_entries()
	batch.reload()
	if not batch.employees:
		log("stage 8: nothing to batch for this period")
		frappe.delete_doc("PieceWork Wage Batch", batch.name, force=True, ignore_permissions=True)
		return None
	if post:
		batch.submit()
		batch.reload()
		log(f"stages 8-9: {batch.name} {batch.status} · {batch.total_employees} employees · net {flt(batch.total_net):,.2f}")
	return batch


# ---------------------------------------------------------------- entry points
def reset():
	"""Remove demo transactions, newest stage first so links never block a cancel."""
	removed = 0
	for doctype in DOCTYPES_IN_ORDER:
		for name in frappe.get_all(doctype, pluck="name", order_by="creation desc"):
			try:
				doc = frappe.get_doc(doctype, name)
				if doc.docstatus == 1:
					doc.flags.ignore_permissions = True
					doc.cancel()
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True,
								  ignore_on_trash=True, delete_permanently=True)
				removed += 1
			except Exception as e:
				log(f"could not remove {doctype} {name}: {e}")
	frappe.db.commit()
	log(f"removed {removed} document(s)")
	return removed


def run(reset_first=False, force=False):
	frappe.flags.mute_emails = True
	company = ensure_company()
	posted = frappe.db.count("PieceWork Wage Batch", {"status": "Posted"})
	if posted and not (reset_first or force):
		frappe.throw(_("This site already has {0} posted wage batch(es). Pass reset_first=True to replace the demo data, or force=True to add to it.").format(posted))
	if reset_first:
		reset()

	department = ensure_department(company)
	workers = ensure_workers(company, department)
	operations = ensure_operations()
	workstation = ensure_workstation()
	configure_settings(company)

	seed_rates(company, operations)
	cell = seed_skills(workers, operations, company, workstation)

	log("closed period (weeks 1-2)")
	closed_days = list(range(-21, -7))
	logs, held = seed_period(company, workstation, workers, operations, cell, closed_days,
							 urgent_day=-16, hold_worker_index=1, approve_hold=True)
	log(f"stages 3-5: {len(logs)} logs, {len(held)} held and approved")
	seed_qc(company, logs, workers)
	seed_batch(company, -21, -8, department=department, post=True)

	log("open period (this week)")
	open_days = list(range(-6, -1))
	open_logs, open_held = seed_period(company, workstation, workers, operations, cell, open_days,
									   hold_worker_index=2, approve_hold=False)
	log(f"stages 3-5: {len(open_logs)} logs, {len(open_held)} still awaiting a manager")
	seed_qc(company, open_logs, workers)

	frappe.db.commit()
	counts = {dt: frappe.db.count(dt, {"docstatus": 1} if frappe.get_meta(dt).is_submittable else {})
			  for dt in DOCTYPES_IN_ORDER}
	log("done: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
	return counts
