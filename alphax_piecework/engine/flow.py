"""The piece-rate process flow: lanes, nodes, edges and how each node's state is read.

This module is the single source of truth for the Process Flow board. The board,
the flow report and the offline tests all read the definitions below, so what is
drawn on screen can never drift from what the app actually enforces.

A flow instance is a payroll period. Select a wage batch and the board shows how
far that period's work has travelled: rates in place, output captured, capacity
holds cleared, QC settled, batch posted. With nothing selected, the board shows
open work per stage across the whole plant.

Node states
-----------
done      the stage finished for this period
current   the stage is where the period is waiting right now
blocked   a prerequisite is missing
pending   not reached yet
"""

import frappe
from frappe import _
from frappe.utils import flt

# ---------------------------------------------------------------- lanes
LANES = [
	{"id": "setup", "en": "Rates & Setup", "ar": "الأسعار والإعداد",
	 "fill": "#DBEAFE", "stroke": "#3B82F6"},
	{"id": "shopfloor", "en": "Shop Floor Capture", "ar": "الالتقاط في الورشة",
	 "fill": "#DCFCE7", "stroke": "#22C55E"},
	{"id": "quality", "en": "Quality Control", "ar": "مراقبة الجودة",
	 "fill": "#FEF3C7", "stroke": "#F59E0B"},
	{"id": "supervision", "en": "Supervision & Approval", "ar": "الإشراف والاعتماد",
	 "fill": "#EDE9FE", "stroke": "#8B5CF6"},
	{"id": "payroll", "en": "Payroll & Posting", "ar": "الرواتب والترحيل",
	 "fill": "#FEE2E2", "stroke": "#EF4444"},
]
LANE_IDS = {lane["id"] for lane in LANES}

# ---------------------------------------------------------------- nodes
# (lane, row) becomes a coordinate in the board renderer.
NODES = [
	{"id": "start", "kind": "terminator", "lane": "setup", "row": 0, "en": "Start", "ar": "البداية"},
	{
		"id": "rates", "kind": "process", "lane": "setup", "row": 1, "step": 1,
		"en": "Approve Piece Rate Matrix", "ar": "اعتماد مصفوفة أسعار القطعة",
		"sub": "Rate per piece, SAM, overtime and slabs",
		"doctype": "Piece Rate Matrix", "report": "Capacity Exceptions", "period": 0,
		"output": ["Effective-dated rates", "Progressive output slabs"],
	},
	{
		"id": "skills", "kind": "process", "lane": "setup", "row": 2, "step": 2,
		"en": "Certify Skills and Production Cells", "ar": "اعتماد المهارات وخلايا الإنتاج",
		"doctype": "Employee Operation Skill", "period": 0,
		"output": ["Certified operations per worker", "Cell membership and split method"],
	},
	{
		"id": "devices", "kind": "process", "lane": "shopfloor", "row": 1, "step": 3,
		"en": "Device and Terminal Capture", "ar": "الالتقاط عبر الأجهزة والطرفيات",
		"sub": "Tablets, scanners, RFID gates, PLC counters",
		"doctype": "PieceWork Edge Event", "date_field": "received_at", "optional": 1,
		"done_filters": {"status": "Processed"},
		"output": ["Offline-safe event queue", "Draft logs per worker and day"],
	},
	{
		"id": "logs", "kind": "process", "lane": "shopfloor", "row": 2, "step": 4,
		"en": "Record Piece Rate Log", "ar": "تسجيل إنتاج العامل",
		"sub": "Desk, Job Card, terminal or device",
		"doctype": "Piece Rate Log", "report": "Piece Rate Earnings Register",
		"output": ["Priced output per worker or cell", "Hours from real timestamps"],
	},
	{
		"id": "d_capacity", "kind": "decision", "lane": "shopfloor", "row": 3,
		"en": "Within Capacity?", "ar": "ضمن الطاقة الإنتاجية؟",
		"doctype": "Piece Rate Log", "filters": {"capacity_status": "Exceeded"},
	},
	{
		"id": "hold", "kind": "process", "lane": "supervision", "row": 3,
		"en": "Hold for Review", "ar": "إيقاف للمراجعة",
		"doctype": "Piece Rate Log", "loop": 1, "filters": {"review_status": "Pending Review"},
	},
	{
		"id": "review", "kind": "process", "lane": "supervision", "row": 4, "step": 5,
		"en": "Supervisor Capacity Review", "ar": "مراجعة المشرف للطاقة",
		"sub": "A second manager approves or rejects",
		"doctype": "Piece Rate Log", "report": "Capacity Exceptions",
		"filters": {"capacity_status": "Exceeded"},
		"done_filters": {"review_status": ["in", ["Approved", "Not Required"]]},
		"output": ["Approved override with a reason", "Segregation of duties"],
	},
	{
		"id": "d_review", "kind": "decision", "lane": "supervision", "row": 5,
		"en": "Override Approved?", "ar": "هل اعتُمد التجاوز؟",
		"doctype": "Piece Rate Log", "filters": {"review_status": "Pending Review"},
	},
	{
		"id": "rejected", "kind": "process", "lane": "shopfloor", "row": 5,
		"en": "Reject and Re-enter", "ar": "رفض وإعادة الإدخال",
		"doctype": "Piece Rate Log", "loop": 1, "filters": {"review_status": "Rejected"},
	},
	{
		"id": "qc", "kind": "process", "lane": "quality", "row": 6, "step": 6,
		"en": "QC Inspection", "ar": "فحص الجودة",
		"sub": "Inspect output, record defect code",
		"doctype": "Shop Floor QA Log", "date_field": "inspection_date", "report": "Defect Pareto",
		"output": ["Defect code and quantity failed", "Evidence against the log"],
	},
	{
		"id": "d_fault", "kind": "decision", "lane": "quality", "row": 7,
		"en": "Worker Responsible?", "ar": "هل العامل مسؤول؟",
		"doctype": "Shop Floor QA Log", "date_field": "inspection_date",
		"filters": {"responsibility": "Worker"},
	},
	{
		"id": "no_fault", "kind": "process", "lane": "supervision", "row": 7,
		"en": "Machine / Material Fault — No Deduction", "ar": "عطل آلة أو مواد — بدون خصم",
		"doctype": "Shop Floor QA Log", "date_field": "inspection_date", "loop": 1,
		"filters": {"responsibility": ["in", ["Machine", "Material Supplier", "Engineering"]]},
	},
	{
		"id": "disposition", "kind": "process", "lane": "quality", "row": 8, "step": 7,
		"en": "Scrap or Rework Disposition", "ar": "إتلاف أو إعادة عمل",
		"sub": "Deduction, material recovery, rework payout",
		"doctype": "Shop Floor QA Log", "date_field": "inspection_date", "report": "Defect Pareto",
		"output": ["Worker deduction", "Rework payout to the specialist"],
	},
	{
		"id": "batch", "kind": "process", "lane": "payroll", "row": 8, "step": 8,
		"en": "Build Wage Batch", "ar": "إنشاء دفعة الأجور",
		"sub": "Fetch entries, slabs, caps, minimum guarantee",
		"doctype": "PieceWork Wage Batch", "date_field": "posting_date",
		"report": "Piece Rate Earnings Register",
		"output": ["Per-employee totals", "Statutory cap waivers and top-ups"],
	},
	{
		"id": "d_posting", "kind": "decision", "lane": "payroll", "row": 9,
		"en": "Posting Successful?", "ar": "هل تم الترحيل بنجاح؟",
		"doctype": "PieceWork Wage Batch", "date_field": "posting_date",
		"filters": {"status": ["in", ["Failed", "Partially Posted"]]},
	},
	{
		"id": "retry", "kind": "process", "lane": "supervision", "row": 9,
		"en": "Fix and Retry Posting", "ar": "التصحيح وإعادة الترحيل",
		"doctype": "PieceWork Wage Batch", "date_field": "posting_date", "loop": 1,
		"filters": {"status": "Failed"},
	},
	{
		"id": "posted", "kind": "process", "lane": "payroll", "row": 10, "step": 9,
		"en": "Post to Ledger or Payroll", "ar": "الترحيل للدفاتر أو الرواتب",
		"sub": "Journal Entry accrual or HRMS Additional Salary",
		"doctype": "PieceWork Wage Batch", "date_field": "posting_date",
		"done_filters": {"status": "Posted"}, "report": "Labor Effectiveness",
		"output": ["Journal Entry or Additional Salary", "Sources locked as Posted"],
	},
	{
		"id": "audit", "kind": "process", "lane": "payroll", "row": 11, "step": 10,
		"en": "Audit and Effectiveness Review", "ar": "التدقيق ومراجعة الفعالية",
		"sub": "Hash-chained trail, OLE and defect analysis",
		"doctype": "PieceWork Audit Event", "date_field": "event_time",
		"report": "Labor Effectiveness",
		"output": ["Tamper-evident audit trail", "OLE and Pareto reporting"],
	},
	{"id": "end", "kind": "terminator", "lane": "payroll", "row": 12, "en": "End", "ar": "النهاية"},
]
NODE_BY_ID = {n["id"]: n for n in NODES}

# ---------------------------------------------------------------- edges
EDGES = [
	{"from": "start", "to": "rates"},
	{"from": "rates", "to": "skills"},
	{"from": "skills", "to": "logs"},
	{"from": "devices", "to": "logs"},
	{"from": "logs", "to": "d_capacity"},
	{"from": "d_capacity", "to": "hold", "label": "No", "ar": "لا"},
	{"from": "hold", "to": "review", "style": "loop"},
	{"from": "d_capacity", "to": "qc", "label": "Yes", "ar": "نعم"},
	{"from": "review", "to": "d_review"},
	{"from": "d_review", "to": "rejected", "label": "No", "ar": "لا"},
	{"from": "rejected", "to": "logs", "style": "loop"},
	{"from": "d_review", "to": "qc", "label": "Yes", "ar": "نعم"},
	{"from": "qc", "to": "d_fault"},
	{"from": "d_fault", "to": "no_fault", "label": "No", "ar": "لا"},
	{"from": "no_fault", "to": "batch", "style": "loop"},
	{"from": "d_fault", "to": "disposition", "label": "Yes", "ar": "نعم"},
	{"from": "disposition", "to": "batch"},
	{"from": "batch", "to": "d_posting"},
	{"from": "d_posting", "to": "retry", "label": "No", "ar": "لا"},
	{"from": "retry", "to": "posted", "style": "loop"},
	{"from": "d_posting", "to": "posted", "label": "Yes", "ar": "نعم"},
	{"from": "posted", "to": "audit"},
	{"from": "audit", "to": "end"},
]

# Stage order used to decide which node a period is waiting at. Device capture is an
# optional input into "logs" - a plant that keys everything at the desk still completes
# the flow - so it carries "optional": 1 and stays out of this list.
SEQUENCE = ["rates", "skills", "logs", "review", "qc", "disposition", "batch", "posted", "audit"]

KEY_OUTPUTS = [
	{"n": 1, "en": "Rates & Setup", "ar": "الأسعار والإعداد", "lane": "setup",
	 "items": ["Effective-dated rate matrix", "Skill certificates", "Production cells"]},
	{"n": 2, "en": "Shop Floor Capture", "ar": "الالتقاط في الورشة", "lane": "shopfloor",
	 "items": ["Device events", "Piece rate logs", "Capacity ceiling result"]},
	{"n": 3, "en": "Quality Control", "ar": "مراقبة الجودة", "lane": "quality",
	 "items": ["Defect codes", "Scrap or rework disposition", "Rework payout"]},
	{"n": 4, "en": "Supervision", "ar": "الإشراف", "lane": "supervision",
	 "items": ["Approved overrides", "Rejected logs", "Posting retries"]},
	{"n": 5, "en": "Payroll & Posting", "ar": "الرواتب والترحيل", "lane": "payroll",
	 "items": ["Wage batch", "Journal Entry or Additional Salary", "Audit trail"]},
]


# ---------------------------------------------------------------- state
def _date_field(node):
	return node.get("date_field") or "posting_date"


def _stage_docs(node, ctx=None, company=None, limit=5, completing=False):
	"""Submitted records that belong to this node, newest first.

	completing=True narrows to the records that actually finish the stage - an
	approved override rather than any held log, a posted batch rather than any batch.
	"""
	doctype = node.get("doctype")
	if not doctype:
		return []
	meta = frappe.get_meta(doctype)
	filters = {}
	if meta.has_field("docstatus") or meta.is_submittable:
		filters["docstatus"] = 1
	filters.update(node.get("filters") or {})
	if completing:
		filters.update(node.get("done_filters") or {})

	field = _date_field(node)
	if doctype == "PieceWork Wage Batch" and ctx:
		# A batch is posted the day after its period closes, so match it by the period it
		# covers - otherwise the batch that paid the week falls outside the week.
		if ctx.get("batch"):
			filters["name"] = ctx["batch"]
		elif ctx.get("from_date"):
			filters["to_date"] = ["between", [ctx["from_date"], ctx["to_date"]]]
	elif ctx and ctx.get("from_date") and node.get("period", 1) and meta.has_field(field):
		filters[field] = ["between", [ctx["from_date"], ctx["to_date"]]]
	if meta.has_field("company"):
		filters["company"] = (ctx or {}).get("company") or company or None
		if not filters["company"]:
			filters.pop("company")

	fields = ["name", "modified"]
	for extra in ("status", "review_status", "capacity_status", "payroll_status", "responsibility",
				  "disposition", "employee_name", "qty_logged", "net_amount", "total_net", "operation"):
		if meta.has_field(extra):
			fields.append(extra)
	order = f"{field} desc" if meta.has_field(field) else "modified desc"
	return frappe.get_all(doctype, filters=filters, fields=fields, order_by=order, limit=limit)


def period_context(batch=None, company=None, from_date=None, to_date=None):
	"""Resolve one flow instance: an explicit batch, an explicit range, or the open period."""
	from frappe.utils import add_days, getdate, today

	ctx = frappe._dict({"batch": None, "company": company, "from_date": from_date,
						"to_date": to_date, "label": None, "status": None})
	if batch:
		row = frappe.db.get_value("PieceWork Wage Batch", batch,
								  ["company", "from_date", "to_date", "status", "posting_date"], as_dict=True)
		if row:
			ctx.update({"batch": batch, "company": row.company, "from_date": row.from_date,
						"to_date": row.to_date, "status": row.status,
						"label": f"{batch} · {row.from_date} – {row.to_date}"})
			return ctx
	if from_date and to_date:
		ctx.label = f"{from_date} – {to_date}"
		return ctx
	# open period: everything captured but not yet carried into a batch
	oldest = frappe.db.sql(
		"""select min(posting_date) from `tabPiece Rate Log`
		where docstatus = 1 and payroll_status = 'Unbatched'""" + (" and company = %s" if company else ""),
		(company,) if company else ())[0][0]
	ctx.from_date = oldest or add_days(getdate(today()), -7)
	ctx.to_date = today()
	ctx.label = _("Open period") + f" · {ctx.from_date} – {ctx.to_date}"
	return ctx


def node_state(node, ctx):
	"""Return (state, count, caption) for one node in one period."""
	nid = node["id"]
	if node["kind"] == "terminator":
		if nid == "start":
			return "done", 0, None
		posted = _stage_docs(NODE_BY_ID["posted"], ctx, limit=1, completing=True)
		return ("done" if posted else "pending"), 0, None

	if node["kind"] == "decision":
		return _decision_state(nid, ctx)

	docs = _stage_docs(node, ctx, limit=500)
	count = len(docs)
	if node.get("loop"):
		return ("current" if count else "pending"), count, None
	if not count:
		return "pending", 0, None
	if not node.get("done_filters"):
		return "done", count, None
	finished = _stage_docs(node, ctx, limit=1, completing=True)
	if finished:
		return "done", count, None
	return "current", count, _("{0} record(s) open, none completed yet").format(count)


def _decision_state(node_id, ctx):
	if node_id == "d_capacity":
		exceeded = len(_stage_docs(NODE_BY_ID["d_capacity"], ctx, limit=500))
		total = len(_stage_docs(NODE_BY_ID["logs"], ctx, limit=500))
		if not total:
			return "pending", 0, None
		if not exceeded:
			return "done", 0, _("Yes - every log is within capacity")
		return "current", exceeded, _("No - {0} log(s) over the ceiling").format(exceeded)
	if node_id == "d_review":
		pending = len(_stage_docs(NODE_BY_ID["hold"], ctx, limit=500))
		rejected = len(_stage_docs(NODE_BY_ID["rejected"], ctx, limit=500))
		if pending:
			return "current", pending, _("{0} log(s) awaiting a manager").format(pending)
		if rejected:
			return "done", rejected, _("No - {0} log(s) rejected and re-entered").format(rejected)
		return "done", 0, _("Yes - nothing left on hold")
	if node_id == "d_fault":
		worker = len(_stage_docs(NODE_BY_ID["d_fault"], ctx, limit=500))
		not_worker = len(_stage_docs(NODE_BY_ID["no_fault"], ctx, limit=500))
		if not (worker or not_worker):
			return "pending", 0, None
		return "done", worker, _("Yes: {0} worker-fault · No: {1} machine or material").format(worker, not_worker)
	if node_id == "d_posting":
		failed = len(_stage_docs(NODE_BY_ID["d_posting"], ctx, limit=500))
		posted = len(_stage_docs(NODE_BY_ID["posted"], ctx, limit=1, completing=True))
		if failed:
			return "current", failed, _("No - {0} batch(es) failed to post").format(failed)
		if posted:
			return "done", 0, _("Yes - posted to the ledger")
		return "pending", 0, None
	return "pending", 0, None


def evaluate(ctx):
	"""State of every node plus the stage the period is waiting at."""
	states = {}
	for node in NODES:
		state, count, caption = node_state(node, ctx)
		states[node["id"]] = {"state": state, "count": count, "caption": caption}

	current = None
	for stage in SEQUENCE:
		if states[stage]["state"] != "done":
			current = stage
			break
	if current:
		idx = SEQUENCE.index(current)
		if idx and states[SEQUENCE[idx - 1]]["state"] != "done":
			states[current]["state"] = "blocked"
		else:
			states[current]["state"] = "current"
	progress = flt(len([s for s in SEQUENCE if states[s]["state"] == "done"]) / len(SEQUENCE) * 100, 1)
	return states, current, progress


def global_counts(company=None):
	"""Open work per node when no period is selected.

	Decision diamonds still report which way the plant's work actually went, so the
	board reads the same whether or not a period is selected.
	"""
	plant = frappe._dict({"batch": None, "company": company, "from_date": None, "to_date": None,
						  "label": None, "status": None})
	out = {}
	for node in NODES:
		if node["kind"] == "decision":
			state, count, caption = _decision_state(node["id"], plant)
			out[node["id"]] = {"state": state, "count": count, "caption": caption}
			continue
		if not node.get("doctype"):
			out[node["id"]] = {"state": "pending", "count": 0, "caption": None}
			continue
		docs = _stage_docs(node, plant, company=company, limit=500)
		out[node["id"]] = {"state": "current" if docs else "pending", "count": len(docs), "caption": None}
	return out
