"""Edge terminal API - tablets, barcode guns, RFID gates, PLC counters.

Offline-first contract
----------------------
* Devices keep a local queue; every event carries a client-generated `client_uuid`.
* POST a batch to `ingest`; the response acknowledges each uuid as accepted,
  duplicate (already stored - safe to drop) or rejected (with reason).
* Devices delete only acknowledged uuids, so replays after Wi-Fi drops never
  double count.
* Ingest is write-only and fast; aggregation into Piece Rate Logs happens in a
  de-duplicated background job.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from alphax_piecework.utils import get_settings

MAX_QTY_PER_EVENT = 100000


def _device_for_session(device_id):
	device = frappe.db.get_value(
		"PieceWork Edge Device", device_id,
		["name", "company", "api_user", "is_active", "workstation", "default_operation"], as_dict=True,
	)
	if not device or not cint(device.is_active):
		frappe.throw(_("Unknown or inactive device."), frappe.PermissionError)
	if device.api_user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Device is not bound to this user."), frappe.PermissionError)
	return device


def _parse(events):
	if isinstance(events, str):
		events = json.loads(events)
	if not isinstance(events, list):
		frappe.throw(_("events must be a list."))
	return events


def _validate_event(event, uid, device, now, skew):
	if not uid:
		raise ValueError("client_uuid is required")
	qty = flt(event.get("qty"))
	if qty <= 0 or qty > MAX_QTY_PER_EVENT:
		raise ValueError("qty must be between 0 and %s" % MAX_QTY_PER_EVENT)
	if not (event.get("employee") or event.get("production_cell")):
		raise ValueError("employee or production_cell is required")
	to_time = get_datetime(event.get("to_time") or event.get("timestamp") or now)
	from_time = get_datetime(event.get("from_time") or to_time)
	if to_time > add_to_date(now, minutes=skew):
		raise ValueError("timestamp is in the future beyond allowed clock skew")
	if from_time > to_time:
		raise ValueError("from_time is after to_time")
	operation = event.get("operation") or device.default_operation
	if not operation:
		raise ValueError("operation is required (no device default)")
	return {
		"client_uuid": uid, "device": device.name, "company": device.company, "status": "Queued",
		"received_at": now, "from_time": from_time, "to_time": to_time,
		"employee": event.get("employee"), "production_cell": event.get("production_cell"),
		"work_order": event.get("work_order"), "operation": operation,
		"workstation": event.get("workstation") or device.workstation, "qty": qty,
		"payload": json.dumps(event, default=str)[:10000],
	}


@frappe.whitelist(methods=["POST"])
def ingest(device_id, events):
	settings = get_settings()
	device = _device_for_session(device_id)
	events = _parse(events)
	limit = cint(settings.max_events_per_request) or 500
	if len(events) > limit:
		frappe.throw(_("Too many events in one request (max {0}).").format(limit))

	uuids = [str(e.get("client_uuid") or "")[:140] for e in events if isinstance(e, dict)]
	existing = set(frappe.get_all("PieceWork Edge Event", filters={"client_uuid": ["in", uuids or [""]]}, pluck="client_uuid"))
	skew = cint(settings.max_clock_skew_minutes) or 15
	now = now_datetime()
	accepted, duplicates, rejected = [], [], []
	seen = set()

	for event in events:
		if not isinstance(event, dict):
			rejected.append({"client_uuid": None, "error": "event must be an object"})
			continue
		uid = str(event.get("client_uuid") or "")[:140]
		if uid and (uid in existing or uid in seen):
			duplicates.append(uid)
			continue
		try:
			values = _validate_event(event, uid, device, now, skew)
		except Exception as e:
			frappe.clear_last_message()
			rejected.append({"client_uuid": uid or None, "error": str(e)[:300]})
			continue
		doc = frappe.get_doc({"doctype": "PieceWork Edge Event", **values})
		doc.flags.ignore_permissions = True
		frappe.db.savepoint("apw_edge")
		try:
			doc.insert()
			accepted.append(uid)
			seen.add(uid)
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point="apw_edge")
			duplicates.append(uid)
		except Exception as e:
			frappe.db.rollback(save_point="apw_edge")
			frappe.clear_last_message()
			rejected.append({"client_uuid": uid, "error": str(e)[:300]})

	frappe.db.set_value("PieceWork Edge Device", device.name, {
		"last_seen": now, "last_ip": getattr(frappe.local, "request_ip", None),
		"last_batch_size": len(events),
		"total_events": cint(frappe.db.get_value("PieceWork Edge Device", device.name, "total_events")) + len(accepted),
	}, update_modified=False)

	if accepted:
		frappe.enqueue("alphax_piecework.engine.edge.process_queued_events", queue="short",
					   job_id="apw_edge_process", deduplicate=True, enqueue_after_commit=True)
	return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "server_time": str(now)}


@frappe.whitelist()
def handshake(device_id):
	"""Device bootstrap: server clock, device defaults, sync limits."""
	device = _device_for_session(device_id)
	settings = get_settings()
	return {
		"server_time": str(now_datetime()),
		"device": device,
		"max_events_per_request": cint(settings.max_events_per_request),
		"max_clock_skew_minutes": cint(settings.max_clock_skew_minutes),
	}


@frappe.whitelist()
def work_orders(device_id, txt=None):
	"""Open Work Orders (with routing) for the device's company."""
	device = _device_for_session(device_id)
	filters = {"company": device.company, "docstatus": 1, "status": ["in", ["Not Started", "In Process"]]}
	if txt:
		filters["name"] = ["like", f"%{txt}%"]
	orders = frappe.get_all("Work Order", filters=filters, fields=["name", "production_item", "item_name", "qty"],
							order_by="modified desc", limit=50)
	ops = {}
	if orders:
		for r in frappe.get_all("Work Order Operation", filters={"parent": ["in", [o.name for o in orders]], "parenttype": "Work Order"},
								fields=["parent", "operation", "workstation"], order_by="idx asc"):
			ops.setdefault(r.parent, []).append({"operation": r.operation, "workstation": r.workstation})
	for o in orders:
		o["operations"] = ops.get(o.name, [])
	return orders


@frappe.whitelist()
def process_now():
	frappe.only_for(("System Manager", "PieceWork Manager"))
	from alphax_piecework.engine.edge import process_queued_events

	return process_queued_events()
