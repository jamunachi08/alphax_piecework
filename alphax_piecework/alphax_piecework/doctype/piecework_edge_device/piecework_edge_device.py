import frappe
from frappe import _
from frappe.model.document import Document

from alphax_piecework.utils import ROLE_DEVICE


class PieceWorkEdgeDevice(Document):
	def validate(self):
		roles = set(frappe.get_roles(self.api_user)) if self.api_user else set()
		if self.api_user in ("Administrator", "Guest"):
			frappe.throw(_("Use a dedicated integration user for devices, not {0}.").format(self.api_user))
		if ROLE_DEVICE not in roles:
			frappe.msgprint(_("User {0} does not have the {1} role yet.").format(self.api_user, ROLE_DEVICE),
							indicator="orange", alert=True)
