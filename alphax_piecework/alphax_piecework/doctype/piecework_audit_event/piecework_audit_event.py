import frappe
from frappe import _
from frappe.model.document import Document


class PieceWorkAuditEvent(Document):
	def validate(self):
		if not self.flags.get("apw_audit_write"):
			frappe.throw(_("Audit events are system-generated and immutable."))

	def on_update(self):
		if not self.flags.get("apw_audit_write"):
			frappe.throw(_("Audit events are immutable."))

	def on_trash(self):
		if not frappe.flags.in_uninstall:
			frappe.throw(_("Audit events cannot be deleted; the hash chain would break."))
