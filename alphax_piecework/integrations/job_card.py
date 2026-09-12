"""Job Card bridge: completed time logs become Piece Rate Logs automatically."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from alphax_piecework.utils import get_settings


def on_submit(doc, method=None):
	if not cint(get_settings().enable_job_card_bridge):
		return
	for tl in doc.get("time_logs") or []:
		if not (tl.employee and flt(tl.completed_qty) > 0 and tl.from_time and tl.to_time):
			continue
		if frappe.db.exists("Piece Rate Log", {"job_card": doc.name, "employee": tl.employee,
											   "from_time": tl.from_time, "docstatus": ["<", 2]}):
			continue
		log = frappe.get_doc({
			"doctype": "Piece Rate Log", "company": doc.company, "posting_date": getdate(tl.from_time),
			"work_order": doc.work_order, "job_card": doc.name, "operation": doc.operation,
			"workstation": doc.workstation, "worker_type": "Individual", "employee": tl.employee,
			"from_time": tl.from_time, "to_time": tl.to_time, "qty_logged": flt(tl.completed_qty), "source": "Job Card",
		})
		log.flags.ignore_permissions = True
		frappe.db.savepoint("apw_jc")
		try:
			log.insert()
		except Exception as e:
			frappe.db.rollback(save_point="apw_jc")
			frappe.clear_last_message()
			doc.add_comment("Comment", _("PieceWork: could not create log for {0}: {1}").format(tl.employee, str(e)[:300]))
			continue
		frappe.db.savepoint("apw_jc_submit")
		try:
			log.submit()
		except Exception as e:
			frappe.db.rollback(save_point="apw_jc_submit")
			frappe.clear_last_message()
			log.add_comment("Comment", _("Left in draft: {0}").format(str(e)[:300]))


def on_cancel(doc, method=None):
	logs = frappe.get_all("Piece Rate Log", filters={"job_card": doc.name, "docstatus": 1},
						  fields=["name", "payroll_status", "wage_batch"])
	batched = [l.name for l in logs if l.payroll_status != "Unbatched"]
	if batched:
		frappe.throw(_("Piece Rate Logs {0} are already in a wage batch. Cancel the batch first.").format(", ".join(batched)))
	for l in logs:
		log = frappe.get_doc("Piece Rate Log", l.name)
		log.flags.ignore_permissions = True
		log.cancel()
	for name in frappe.get_all("Piece Rate Log", filters={"job_card": doc.name, "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Piece Rate Log", name, ignore_permissions=True)
