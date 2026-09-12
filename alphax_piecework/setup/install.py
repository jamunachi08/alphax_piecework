"""Idempotent seeders - safe on every migrate (no fixtures)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from alphax_piecework.utils import (
	ROLE_DEVICE, ROLE_MANAGER, ROLE_QC, ROLE_SUPERVISOR, SETTINGS_DEFAULTS, SETTINGS_DOCTYPE,
)

ROLES = {
	ROLE_MANAGER: "Configures rates, approves capacity overrides, runs wage batches.",
	ROLE_SUPERVISOR: "Records and submits production logs for their lines.",
	ROLE_QC: "Records shop-floor inspections and defect dispositions.",
	ROLE_DEVICE: "Integration role for shop-floor devices (API only).",
}

CUSTOM_FIELDS = {
	"Workstation": [
		{"fieldname": "apw_section", "fieldtype": "Section Break", "label": "PieceWork", "insert_after": "hour_rate",
		 "collapsible": 1},
		{"fieldname": "apw_max_pieces_per_hour", "fieldtype": "Float", "label": "Max Rated Pieces / Hour",
		 "insert_after": "apw_section", "description": "Physical ceiling used by PieceWork capacity throttling."},
	],
	"Operation": [
		{"fieldname": "apw_default_sam_minutes", "fieldtype": "Float", "label": "Default SAM (minutes / piece)",
		 "insert_after": "workstation", "description": "Fallback when the Piece Rate Matrix row has no SAM."},
	],
	"Work Order": [
		{"fieldname": "apw_is_urgent", "fieldtype": "Check", "label": "Urgent (PieceWork premium)",
		 "insert_after": "company", "allow_on_submit": 1},
	],
	"Employee": [
		{"fieldname": "apw_piece_rate_eligible", "fieldtype": "Check", "label": "Piece-rate Eligible",
		 "insert_after": "department", "default": "0"},
		{"fieldname": "apw_default_production_cell", "fieldtype": "Link", "label": "Default Production Cell",
		 "options": "Production Cell", "insert_after": "apw_piece_rate_eligible",
		 "depends_on": "eval:doc.apw_piece_rate_eligible"},
	],
}

DIFFICULTY = [
	("Standard", "قياسي", 1.0),
	("Complex", "معقد", 1.25),
	("Critical", "حرج", 1.5),
]

DEFECTS = [
	# code, name EN, name AR, category, responsibility, disposition, severity
	("WM-STITCH", "Skipped / broken stitch", "غرزة مفقودة أو مقطوعة", "Workmanship", "Worker", "Rework - Original Worker", "Minor"),
	("WM-DIM", "Out of dimensional tolerance", "خارج حدود التفاوت البعدي", "Workmanship", "Worker", "Rework - Third Party", "Major"),
	("WM-SOLDER", "Solder bridge / cold joint", "جسر لحام أو لحام بارد", "Workmanship", "Worker", "Rework - Third Party", "Major"),
	("HD-SCRATCH", "Surface damage in handling", "تلف سطحي أثناء المناولة", "Handling", "Worker", "Scrap", "Minor"),
	("MC-FAULT", "Machine fault", "عطل في الآلة", "Machine", "Machine", "Rework - Third Party", "Major"),
	("MT-DEFECT", "Raw material defect", "عيب في المادة الخام", "Material", "Material Supplier", "Scrap", "Major"),
	("DS-SPEC", "Design / specification error", "خطأ في التصميم أو المواصفات", "Design", "Engineering", "Scrap", "Critical"),
]


def after_install():
	run_seeders()


def after_migrate():
	run_seeders()


def run_seeders():
	seed_roles()
	create_custom_fields(CUSTOM_FIELDS, update=True)
	seed_masters()
	seed_settings()
	frappe.db.commit()


def seed_roles():
	for role, description in ROLES.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0 if role == ROLE_DEVICE else 1,
							"description": description}).insert(ignore_permissions=True)


def seed_masters():
	for name, name_ar, mult in DIFFICULTY:
		if not frappe.db.exists("PieceWork Difficulty Class", name):
			frappe.get_doc({"doctype": "PieceWork Difficulty Class", "class_name": name, "class_name_ar": name_ar,
							"rate_multiplier": mult}).insert(ignore_permissions=True)
	for code, name, name_ar, category, resp, disp, sev in DEFECTS:
		if not frappe.db.exists("PieceWork Defect Code", code):
			frappe.get_doc({"doctype": "PieceWork Defect Code", "defect_code": code, "defect_name": name,
							"defect_name_ar": name_ar, "category": category, "responsibility": resp,
							"default_disposition": disp, "severity": sev, "is_active": 1}).insert(ignore_permissions=True)


def seed_settings():
	"""Write defaults only for keys that were never stored - never overwrite user choices."""
	stored = set(frappe.db.sql_list("select field from `tabSingles` where doctype=%s", SETTINGS_DOCTYPE))
	for key, value in SETTINGS_DEFAULTS.items():
		if key not in stored:
			frappe.db.set_single_value(SETTINGS_DOCTYPE, key, value, update_modified=False)
	frappe.clear_cache(doctype=SETTINGS_DOCTYPE)


def before_uninstall():
	frappe.flags.in_uninstall = True
	for doctype, fields in CUSTOM_FIELDS.items():
		for f in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": f["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
