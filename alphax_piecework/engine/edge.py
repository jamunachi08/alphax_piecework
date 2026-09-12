"""Aggregates queued edge events into draft Piece Rate Logs.

One open draft per (date, worker, work order, operation, workstation, device);
counts accumulate and the time window stretches. Drafts are submitted by a
supervisor or automatically the next day (PieceWork Settings) - that is where
the full anti-fraud validation runs.
"""

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, today

from alphax_piecework.utils import get_settings

BATCH = 1000


def process_queued_events():
	events = frappe.db.sql(
		"""select name from `tabPieceWork Edge Event` where status = 'Queued'
		order by to_time asc limit %s for update skip locked""",
		BATCH, pluck=True,
	)
	for name in events:
		ev = frappe.get_doc("PieceWork Edge Event", name)
		try:
			frappe.db.savepoint("apw_ev")
			log_name = _apply_event(ev)
			ev.db_set({"status": "Processed", "piece_rate_log": log_name, "error": None,
					   "attempts": cint(ev.attempts) + 1}, update_modified=False)
		except Exception as e:
			frappe.db.rollback(save_point="apw_ev")
			frappe.clear_last_message()
			ev.db_set({"status": "Failed", "error": str(e)[:1000], "attempts": cint(ev.attempts) + 1}, update_modified=False)
	frappe.db.commit()
	return len(events)


def _apply_event(ev):
	posting_date = getdate(ev.to_time)
	filters = {
		"docstatus": 0, "source": "Edge Device", "edge_device": ev.device, "company": ev.company,
		"posting_date": posting_date, "operation": ev.operation,
		"work_order": ev.work_order or ["is", "not set"],
		"workstation": ev.workstation or ["is", "not set"],
	}
	if ev.employee:
		filters.update({"worker_type": "Individual", "employee": ev.employee})
	else:
		filters.update({"worker_type": "Production Cell", "production_cell": ev.production_cell})
	existing = frappe.get_all("Piece Rate Log", filters=filters, pluck="name", limit=1)

	if existing:
		log = frappe.get_doc("Piece Rate Log", existing[0])
		log.qty_logged = flt(log.qty_logged) + flt(ev.qty)
		log.from_time = min(get_datetime(log.from_time), get_datetime(ev.from_time))
		log.to_time = max(get_datetime(log.to_time), get_datetime(ev.to_time))
		if log.worker_type == "Production Cell":
			log.set("splits", [])  # re-derive member hours for the stretched window
	else:
		log = frappe.get_doc({
			"doctype": "Piece Rate Log", "company": ev.company, "posting_date": posting_date,
			"source": "Edge Device", "edge_device": ev.device, "work_order": ev.work_order,
			"operation": ev.operation, "workstation": ev.workstation,
			"worker_type": "Individual" if ev.employee else "Production Cell",
			"employee": ev.employee, "production_cell": ev.production_cell,
			"from_time": ev.from_time, "to_time": ev.to_time, "qty_logged": flt(ev.qty),
		})
	if get_datetime(log.from_time) == get_datetime(log.to_time):
		# single scan: give the window one minute so hours are non-zero
		from frappe.utils import add_to_date
		log.from_time = add_to_date(get_datetime(log.to_time), minutes=-1)
	log.flags.ignore_permissions = True
	log.flags.ignore_mandatory = False
	log.save()
	return log.name


def auto_submit_edge_logs():
	settings = get_settings()
	if not cint(settings.auto_submit_edge_logs):
		return
	for name in frappe.get_all("Piece Rate Log", filters={"docstatus": 0, "source": "Edge Device", "posting_date": ["<", today()]},
							   pluck="name", limit=5000):
		log = frappe.get_doc("Piece Rate Log", name)
		try:
			frappe.db.savepoint("apw_auto")
			log.flags.ignore_permissions = True
			log.submit()
		except Exception as e:
			frappe.db.rollback(save_point="apw_auto")
			frappe.clear_last_message()
			log.add_comment("Comment", f"Auto-submit failed: {str(e)[:500]}")
	frappe.db.commit()


def purge_processed_events():
	days = cint(get_settings().edge_event_retention_days) or 30
	frappe.db.delete("PieceWork Edge Event", {"status": "Processed", "creation": ["<", add_days(now_datetime(), -days)]})
	frappe.db.commit()


@frappe.whitelist()
def retry_failed_events():
	frappe.only_for(("System Manager", "PieceWork Manager"))
	count = frappe.db.sql("update `tabPieceWork Edge Event` set status='Queued' where status='Failed'")
	frappe.enqueue("alphax_piecework.engine.edge.process_queued_events", queue="short",
				   job_id="apw_edge_process", deduplicate=True, enqueue_after_commit=True)
	return "ok"
