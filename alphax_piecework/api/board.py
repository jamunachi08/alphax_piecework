"""API behind the PieceWork Process Flow board.

The board is not a picture: every node reports live state from the same
definitions the controllers enforce, and every action it offers is a real
permission-checked transaction. A flow instance is a payroll period.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from alphax_piecework.engine import flow
from alphax_piecework.utils import get_settings


def _can(doctype, ptype="read"):
	return bool(doctype) and frappe.has_permission(doctype, ptype)


def _node_payload(node, state):
	payload = {
		"id": node["id"], "kind": node["kind"], "lane": node["lane"], "row": node["row"],
		"step": node.get("step"), "en": node["en"], "ar": node.get("ar"), "sub": node.get("sub"),
		"doctype": node.get("doctype"), "report": node.get("report"), "loop": node.get("loop", 0),
		"output": node.get("output") or [],
	}
	payload.update(state)
	if node.get("doctype"):
		payload["can_read"] = _can(node["doctype"])
		payload["can_create"] = _can(node["doctype"], "create")
	return payload


@frappe.whitelist()
def get_board(batch=None, company=None, from_date=None, to_date=None):
	"""Full board definition plus live state.

	With no period selected the board shows open work per stage across the plant;
	with a batch or a date range it shows how far that period has travelled.
	"""
	company = company or _default_company()
	settings = get_settings()
	selected = bool(batch or (from_date and to_date))
	ctx = flow.period_context(batch=batch, company=company, from_date=from_date, to_date=to_date)

	if selected:
		states, current, progress = flow.evaluate(ctx)
		mode = "period"
	else:
		states, current, progress = flow.global_counts(company), None, 0.0
		mode = "plant"

	nodes = [_node_payload(n, states.get(n["id"], {})) for n in flow.NODES]
	return {
		"mode": mode, "lanes": flow.LANES, "nodes": nodes, "edges": flow.EDGES,
		"sequence": flow.SEQUENCE, "key_outputs": flow.KEY_OUTPUTS,
		"current": current, "progress": progress,
		"context": {"batch": ctx.batch, "company": ctx.company, "from_date": str(ctx.from_date),
					"to_date": str(ctx.to_date), "title": ctx.label, "status": ctx.status},
		"show_financials": cint(settings.show_financials_on_board) if settings.get("show_financials_on_board") is not None else 1,
		"refresh_seconds": cint(settings.get("board_refresh_seconds")) or 120,
		"currency": frappe.get_cached_value("Company", ctx.company, "default_currency") if ctx.company else None,
	}


@frappe.whitelist()
def get_node_detail(node_id, batch=None, company=None, from_date=None, to_date=None, limit=10):
	"""Records behind one node, with the next actions the user may actually take."""
	node = flow.NODE_BY_ID.get(node_id)
	if not node:
		frappe.throw(_("Unknown stage."))
	company = company or _default_company()
	selected = bool(batch or (from_date and to_date))
	ctx = flow.period_context(batch=batch, company=company, from_date=from_date, to_date=to_date)
	doctype = node.get("doctype")
	if doctype and not _can(doctype):
		frappe.throw(_("You do not have access to {0}.").format(_(doctype)), frappe.PermissionError)

	docs = flow._stage_docs(node, ctx if selected else None, company=company, limit=cint(limit))
	drafts = []
	if doctype:
		meta = frappe.get_meta(doctype)
		if meta.is_submittable:
			filters = {"docstatus": 0}
			if meta.has_field("company"):
				filters["company"] = ctx.company
			drafts = frappe.get_all(doctype, filters=filters, fields=["name", "modified"],
									order_by="modified desc", limit=5)
	state, count, caption = (flow.node_state(node, ctx) if selected
							 else ("current" if docs else "pending", len(docs), None))
	return {
		"node": _node_payload(node, {"state": state, "count": count, "caption": caption}),
		"documents": docs, "drafts": drafts,
		"list_filters": _list_filters(node, ctx, selected),
		"new_defaults": _new_defaults(node, ctx),
		"report_filters": _report_filters(node, ctx) if node.get("report") else None,
	}


def _list_filters(node, ctx, selected=True):
	doctype = node.get("doctype")
	if not doctype:
		return {}
	filters = dict(node.get("filters") or {})
	meta = frappe.get_meta(doctype)
	if meta.has_field("company") and ctx.company:
		filters["company"] = ctx.company
	field = flow._date_field(node)
	if selected and node.get("period", 1) and meta.has_field(field) and ctx.from_date:
		filters[field] = ["between", [str(ctx.from_date), str(ctx.to_date)]]
	return filters


def _report_filters(node, ctx):
	filters = {"company": ctx.company}
	if ctx.from_date:
		filters.update({"from_date": str(ctx.from_date), "to_date": str(ctx.to_date)})
	return filters


def _new_defaults(node, ctx):
	"""Pre-fill a new document so the board's New button lands on a usable form."""
	doctype = node.get("doctype")
	if not doctype or not _can(doctype, "create"):
		return None
	meta = frappe.get_meta(doctype)
	values = {}
	if ctx.company and meta.has_field("company"):
		values["company"] = ctx.company
	if node["id"] == "batch" and ctx.from_date:
		values.update({"from_date": str(ctx.from_date), "to_date": str(ctx.to_date)})
	for field, value in (node.get("filters") or {}).items():
		if isinstance(value, (str, int)) and meta.has_field(field):
			values[field] = value
	return values


def _default_company():
	return (frappe.defaults.get_user_default("Company")
			or frappe.db.get_value("Company", {}, "name"))


@frappe.whitelist()
def get_flow_instances(company=None, limit=20):
	"""Periods the board can show: recent wage batches plus the open, unbatched period."""
	company = company or _default_company()
	out = []
	ctx = flow.period_context(company=company)
	states, current, progress = flow.evaluate(ctx)
	out.append({"type": "Open", "key": "", "title": ctx.label, "status": _("Unbatched"),
				"from_date": str(ctx.from_date), "to_date": str(ctx.to_date),
				"current": current,
				"current_label": flow.NODE_BY_ID[current]["en"] if current else _("Complete"),
				"progress": progress, "value": 0})
	batches = frappe.get_all(
		"PieceWork Wage Batch",
		filters={"docstatus": ["<", 2], "company": company},
		fields=["name", "from_date", "to_date", "status", "total_net", "total_employees"],
		order_by="from_date desc", limit=cint(limit))
	for row in batches:
		bctx = flow.period_context(batch=row.name)
		states, current, progress = flow.evaluate(bctx)
		out.append({"type": "Batch", "key": row.name,
					"title": f"{row.name} · {row.from_date} – {row.to_date}",
					"status": row.status, "from_date": str(row.from_date), "to_date": str(row.to_date),
					"value": flt(row.total_net), "employees": row.total_employees,
					"current": current,
					"current_label": flow.NODE_BY_ID[current]["en"] if current else _("Complete"),
					"progress": progress})
	return out


@frappe.whitelist()
def get_stage_summary(company=None):
	"""Counts per numbered stage, for the workspace and the board header."""
	company = company or _default_company()
	counts = flow.global_counts(company)
	return [{"id": n["id"], "en": n["en"], "step": n.get("step"), "count": counts[n["id"]]["count"]}
			for n in flow.NODES if n.get("step")]
