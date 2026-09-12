import frappe
from frappe import _
from frappe.utils import cint, flt

SETTINGS_DOCTYPE = "PieceWork Settings"

ROLE_MANAGER = "PieceWork Manager"
ROLE_SUPERVISOR = "PieceWork Supervisor"
ROLE_QC = "PieceWork QC Inspector"
ROLE_DEVICE = "PieceWork Device"

# Fallbacks used when a key has never been written to tabSingles (seeder writes them too).
SETTINGS_DEFAULTS = {
	"standard_shift_hours": 8,
	"require_time_window": 1,
	"block_overlapping_logs": 1,
	"enable_job_card_bridge": 0,
	"enable_capacity_check": 1,
	"capacity_basis": "Stricter of Both",
	"max_efficiency_percent": 130,
	"capacity_action": "Hold for Review",
	"enable_material_balance": 1,
	"check_material_transferred": 1,
	"enable_flow_balance": 1,
	"require_operation_skill": 0,
	"multi_skill_min_operations": 3,
	"enforce_attendance": 0,
	"default_scrap_payout_percent": 0,
	"default_rework_original_payout_percent": 0,
	"material_cost_recovery_percent": 0,
	"default_third_party_deduction_percent": 100,
	"default_third_party_payout_percent": 100,
	"posting_mode": "Journal Entry Accrual",
	"enqueue_threshold": 50,
	"enable_deduction_caps": 1,
	"damage_deduction_cap_days": 5,
	"total_deduction_cap_percent": 50,
	"enable_minimum_guarantee": 0,
	"auto_submit_edge_logs": 1,
	"edge_event_retention_days": 30,
	"max_events_per_request": 500,
	"max_clock_skew_minutes": 15,
	"leaderboard_refresh_seconds": 30,
	"leaderboard_show_names": 1,
}


def get_settings():
	"""Cached settings as a frappe._dict; keys never written fall back to SETTINGS_DEFAULTS."""
	doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	stored = set(frappe.db.sql_list("select field from `tabSingles` where doctype=%s", SETTINGS_DOCTYPE))
	out = frappe._dict(doc.as_dict())
	for key, default in SETTINGS_DEFAULTS.items():
		# Check/Int fields of a Single load as 0 when never saved - only trust stored keys.
		if key not in stored:
			out[key] = default
	# Blank allowance means "inherit Manufacturing Settings" - keep it distinguishable from an explicit 0.
	if "over_production_allowance_percent" not in stored:
		out["over_production_allowance_percent"] = None
	return out


def hrms_installed() -> bool:
	return "hrms" in frappe.get_installed_apps()


def get_company_accounts(company: str):
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	for row in settings.get("company_accounts") or []:
		if row.company == company:
			return row
	return None


def require_company_accounts(company: str):
	row = get_company_accounts(company)
	if not row:
		frappe.throw(
			_("Configure wage accounts for company {0} in PieceWork Settings > Company Accounts.").format(
				frappe.bold(company)
			),
			title=_("Accounts Missing"),
		)
	return row


def has_any_role(*roles) -> bool:
	user_roles = set(frappe.get_roles())
	return bool(user_roles.intersection(set(roles) | {"System Manager"}))


def assert_role(*roles):
	if not has_any_role(*roles):
		frappe.throw(_("Not permitted. Requires one of: {0}").format(", ".join(roles)), frappe.PermissionError)


def currency_of(company: str) -> str:
	return frappe.get_cached_value("Company", company, "default_currency")


def over_production_allowance(settings=None) -> float:
	settings = settings or get_settings()
	if settings.get("over_production_allowance_percent") not in (None, ""):
		return flt(settings.over_production_allowance_percent)
	return flt(frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order"))


def truthy(value) -> bool:
	return bool(cint(value))
