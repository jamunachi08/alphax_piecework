"""Hash-chained audit trail.

Every sensitive action (capacity override, rate amendment, deduction waiver, batch
posting) appends a PieceWork Audit Event whose hash covers the previous event's
hash, so any later edit/deletion of history is detectable with verify_chain().
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

DOCTYPE = "PieceWork Audit Event"
GENESIS = "0" * 64


def _digest(payload: dict) -> str:
	return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def record(reference_doctype, reference_name, action, reason=None, before=None, after=None):
	last = frappe.db.sql(
		f"select `sequence`, event_hash from `tab{DOCTYPE}` order by `sequence` desc limit 1 for update",
		as_dict=True,
	)
	sequence = cint(last[0].sequence) + 1 if last else 1
	previous_hash = last[0].event_hash if last else GENESIS
	event_time = now_datetime()
	body = {
		"sequence": sequence,
		"event_time": event_time,
		"user": frappe.session.user,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"action": action,
		"reason": reason or "",
		"before_json": json.dumps(before, sort_keys=True, default=str) if before is not None else "",
		"after_json": json.dumps(after, sort_keys=True, default=str) if after is not None else "",
		"previous_hash": previous_hash,
	}
	body["event_hash"] = _digest(body)
	doc = frappe.get_doc({"doctype": DOCTYPE, **body})
	doc.flags.ignore_permissions = True
	doc.flags.apw_audit_write = True
	doc.insert()
	return doc.name


@frappe.whitelist()
def verify_chain():
	"""Returns {"ok": bool, "checked": n, "broken_at": sequence|None}."""
	frappe.only_for(("System Manager", "PieceWork Manager"))
	previous = GENESIS
	checked = 0
	for row in frappe.db.sql(
		f"""select `sequence`, event_time, `user`, reference_doctype, reference_name, action, reason,
		before_json, after_json, previous_hash, event_hash from `tab{DOCTYPE}` order by `sequence` asc""",
		as_dict=True,
	):
		body = {k: row[k] for k in row if k != "event_hash"}
		for key in ("reason", "before_json", "after_json"):
			body[key] = body[key] or ""
		if row.previous_hash != previous or _digest(body) != row.event_hash:
			return {"ok": False, "checked": checked, "broken_at": row.sequence}
		previous = row.event_hash
		checked += 1
	return {"ok": True, "checked": checked, "broken_at": None}
