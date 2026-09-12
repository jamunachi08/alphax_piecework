"""Anti-fraud validation layer for Piece Rate Logs.

Improvements over a naive "pieces <= shift x speed" hook:
  * hours come from real timestamps (no silent 8-hour default)
  * SAM-based ceilings with a plausible-efficiency cap, scaled by worker skill
  * material balance is per *operation* (not summed across operations) and counts
    every other submitted log, under a row lock on the Work Order to stop races
  * flow balance: an operation cannot out-produce the operation before it
  * overlapping time windows for the same worker are rejected (ghost double-logging)
  * exceeded capacity can be routed to a review queue instead of a hard block
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, getdate, get_datetime, now_datetime

from alphax_piecework.engine import calc
from alphax_piecework.utils import get_settings, hrms_installed, over_production_allowance

ACTIVE_REVIEW = ("Not Required", "Pending Review", "Approved")


def involved_employees(doc):
	if doc.worker_type == "Individual":
		return [doc.employee] if doc.employee else []
	return [row.employee for row in doc.get("splits") or [] if row.employee]


# ------------------------------------------------------------------ time window
def validate_time_window(doc, settings):
	if doc.from_time and doc.to_time:
		start, end = get_datetime(doc.from_time), get_datetime(doc.to_time)
		if end <= start:
			frappe.throw(_("To Time must be after From Time."))
		if end > add_to_date(now_datetime(), minutes=cint(settings.max_clock_skew_minutes) or 15):
			frappe.throw(_("To Time cannot be in the future."))
		hours = calc.hours_between(start, end)
		if hours > 24:
			frappe.throw(_("A single log cannot span more than 24 hours."))
		doc.hours_worked = hours
	elif cint(settings.require_time_window):
		frappe.throw(_("From Time and To Time are required (PieceWork Settings)."), title=_("Time Window Required"))

	if flt(doc.overtime_hours) < 0 or flt(doc.hours_worked) < 0:
		frappe.throw(_("Hours cannot be negative."))
	if flt(doc.overtime_hours) > flt(doc.hours_worked):
		frappe.throw(_("Overtime Hours cannot exceed Hours Worked."))


def validate_overlap(doc, settings):
	if not cint(settings.block_overlapping_logs) or not (doc.from_time and doc.to_time):
		return
	employees = involved_employees(doc)
	if not employees:
		return
	clash = frappe.db.sql(
		"""
		select distinct l.name from `tabPiece Rate Log` l
		left join `tabPiece Rate Log Split` s on s.parent = l.name and s.parenttype = 'Piece Rate Log'
		where l.docstatus < 2 and l.name != %(name)s and l.review_status != 'Rejected'
		  and l.from_time < %(to_time)s and l.to_time > %(from_time)s
		  and (l.employee in %(emps)s or s.employee in %(emps)s)
		limit 3
		""",
		{"name": doc.name or "", "from_time": doc.from_time, "to_time": doc.to_time, "emps": tuple(employees)},
		pluck=True,
	)
	if clash:
		frappe.throw(
			_("Worker time overlaps with existing log(s): {0}").format(", ".join(clash)),
			title=_("Overlapping Time Window"),
		)


# ------------------------------------------------------------------ skills
def skill_rows(employees, operation, on_date):
	if not employees:
		return {}
	rows = frappe.db.sql(
		"""
		select employee, capacity_factor_percent, skill_level from `tabEmployee Operation Skill`
		where is_active = 1 and operation = %(op)s and employee in %(emps)s
		  and (certified_on is null or certified_on <= %(d)s) and (valid_till is null or valid_till >= %(d)s)
		""",
		{"op": operation, "emps": tuple(employees), "d": getdate(on_date)},
		as_dict=True,
	)
	return {r.employee: r for r in rows}


def validate_skills(doc, settings):
	if not cint(settings.require_operation_skill):
		return
	employees = involved_employees(doc)
	valid = skill_rows(employees, doc.operation, doc.posting_date)
	missing = [e for e in employees if e not in valid]
	if missing:
		frappe.throw(
			_("No valid skill certification for operation {0}: {1}").format(frappe.bold(doc.operation), ", ".join(missing)),
			title=_("Skill Required"),
		)


def multi_skill_eligible_ratio(doc, settings):
	"""Share (0..1) of the workforce on this log holding >= N active operation skills."""
	employees = involved_employees(doc)
	need = cint(settings.multi_skill_min_operations) or 3
	if not employees:
		return 0.0
	counts = dict(
		frappe.db.sql(
			"""
			select employee, count(distinct operation) from `tabEmployee Operation Skill`
			where is_active = 1 and employee in %(emps)s
			  and (valid_till is null or valid_till >= %(d)s)
			group by employee
			""",
			{"emps": tuple(employees), "d": getdate(doc.posting_date)},
		)
	)
	eligible = sum(1 for e in employees if cint(counts.get(e)) >= need)
	return eligible / len(employees)


# ------------------------------------------------------------------ attendance
def validate_attendance(doc, settings):
	if not cint(settings.enforce_attendance) or not hrms_installed():
		return
	missing = []
	for emp in involved_employees(doc):
		present = frappe.db.exists(
			"Attendance",
			{"employee": emp, "attendance_date": doc.posting_date, "docstatus": 1,
			 "status": ["in", ["Present", "Half Day", "Work From Home"]]},
		)
		if not present:
			present = frappe.db.sql(
				"select name from `tabEmployee Checkin` where employee=%s and date(time)=%s limit 1",
				(emp, doc.posting_date),
			)
		if not present:
			missing.append(emp)
	if missing:
		frappe.throw(
			_("No attendance or check-in on {0} for: {1}").format(doc.posting_date, ", ".join(missing)),
			title=_("Attendance Required"),
		)


# ------------------------------------------------------------------ capacity
def evaluate_capacity(doc, settings):
	"""Sets capacity_limit / capacity_basis / capacity_status. Returns True if exceeded."""
	if not cint(settings.enable_capacity_check):
		doc.capacity_status = "Not Configured"
		return False
	rated = 0
	if doc.workstation:
		rated = flt(frappe.get_cached_value("Workstation", doc.workstation, "apw_max_pieces_per_hour"))
	factor = 100.0
	employees = involved_employees(doc)
	if employees:
		skills = skill_rows(employees, doc.operation, doc.posting_date)
		factors = [flt(skills[e].capacity_factor_percent) or 100 if e in skills else 100 for e in employees]
		factor = sum(factors) / len(factors)
	# Cells: ceiling scales with head-count on the log (each member runs the cycle).
	hours = flt(doc.hours_worked)
	if doc.worker_type == "Production Cell" and doc.get("splits"):
		hours = sum(flt(r.hours) for r in doc.splits) or hours * len(doc.splits)

	limit, basis = calc.capacity_limit(
		hours, rated, flt(doc.sam_minutes), flt(settings.max_efficiency_percent), settings.capacity_basis, factor
	)
	doc.capacity_limit = limit or 0
	doc.capacity_basis = basis or ""
	if limit is None:
		doc.capacity_status = "Not Configured"
		return False
	exceeded = flt(doc.qty_logged) > flt(limit)
	doc.capacity_status = "Exceeded" if exceeded else "Within Capacity"
	return exceeded


def enforce_capacity_on_submit(doc, settings):
	if doc.capacity_status != "Exceeded":
		if doc.review_status == "Pending Review":
			doc.review_status = "Not Required"
		return
	msg = _("Logged pieces ({0}) exceed the plausible ceiling ({1}) for {2} h ({3}).").format(
		flt(doc.qty_logged), flt(doc.capacity_limit), flt(doc.hours_worked), doc.capacity_basis
	)
	action = settings.capacity_action
	if action == "Block":
		frappe.throw(msg, title=_("Capacity Exceeded"))
	elif action == "Hold for Review":
		doc.review_status = "Pending Review"
		doc.validation_notes = msg
		frappe.msgprint(msg + " " + _("The log is held for manager review and excluded from payroll until approved."),
			indicator="orange", alert=True)
	else:
		doc.validation_notes = msg
		frappe.msgprint(msg, indicator="orange", alert=True)


# ------------------------------------------------------------------ material & flow
def _logged_for_operation(work_order, operation, exclude):
	return flt(
		frappe.db.sql(
			"""
			select sum(qty_logged - ifnull(qty_rejected, 0)) from `tabPiece Rate Log`
			where docstatus = 1 and work_order = %s and operation = %s and name != %s and review_status != 'Rejected'
			""",
			(work_order, operation, exclude or ""),
		)[0][0]
	)


def validate_material_and_flow(doc, settings):
	if not doc.work_order:
		return
	# Row lock serialises concurrent submissions against the same Work Order.
	wo = frappe.db.sql(
		"""select name, qty, docstatus, status, skip_transfer, material_transferred_for_manufacturing, company, bom_no
		from `tabWork Order` where name = %s for update""",
		doc.work_order,
		as_dict=True,
	)
	if not wo:
		frappe.throw(_("Work Order {0} not found.").format(doc.work_order))
	wo = wo[0]
	if wo.docstatus != 1:
		frappe.throw(_("Work Order {0} must be submitted.").format(doc.work_order))
	if wo.status in ("Stopped", "Closed", "Cancelled"):
		frappe.throw(_("Work Order {0} is {1}.").format(doc.work_order, wo.status))
	if wo.company != doc.company:
		frappe.throw(_("Work Order belongs to a different company."))

	allowance = 1 + over_production_allowance(settings) / 100.0
	already = _logged_for_operation(doc.work_order, doc.operation, doc.name)
	total = already + flt(doc.qty_logged)

	routing = frappe.get_all(
		"Work Order Operation",
		filters={"parent": doc.work_order, "parenttype": "Work Order"},
		fields=["operation", "idx", "sequence_id"],
		order_by="idx asc",
	)
	if not routing and wo.bom_no:
		# Work Orders created via API/import may not carry the routing table - fall back to the BOM.
		routing = frappe.get_all(
			"BOM Operation",
			filters={"parent": wo.bom_no, "parenttype": "BOM"},
			fields=["operation", "idx", "sequence_id"],
			order_by="idx asc",
		)
	if routing and doc.operation not in [r.operation for r in routing]:
		frappe.throw(_("Operation {0} is not in the routing of Work Order {1}.").format(
			frappe.bold(doc.operation), doc.work_order), title=_("Invalid Operation"))

	if cint(settings.enable_material_balance):
		ceiling = flt(wo.qty) * allowance
		if total > ceiling:
			frappe.throw(
				_("Operation {0} would reach {1} pieces on Work Order {2}; limit is {3} (qty {4} + allowance).").format(
					frappe.bold(doc.operation), total, doc.work_order, flt(ceiling, 2), flt(wo.qty)
				),
				title=_("Material Balance"),
			)

	if cint(settings.check_material_transferred) and not cint(wo.skip_transfer):
		transferred = flt(wo.material_transferred_for_manufacturing) * allowance
		if total > transferred:
			frappe.throw(
				_("Pieces for {0} ({1}) exceed material transferred for manufacturing ({2}) on {3}.").format(
					frappe.bold(doc.operation), total, flt(wo.material_transferred_for_manufacturing), doc.work_order
				),
				title=_("Material Not Issued"),
			)

	if cint(settings.enable_flow_balance) and routing:
		ops = [r.operation for r in routing]
		pos = ops.index(doc.operation)
		if pos > 0:
			previous_op = ops[pos - 1]
			upstream = _logged_for_operation(doc.work_order, previous_op, "")
			if total > upstream * allowance:
				frappe.throw(
					_("Operation {0} ({1} pieces) cannot exceed accepted output of the previous operation {2} ({3}).").format(
						frappe.bold(doc.operation), total, frappe.bold(previous_op), upstream
					),
					title=_("Flow Balance"),
				)
