app_name = "alphax_piecework"
app_title = "AlphaX PieceWork"
app_publisher = "Neotec Integrated Solutions"
app_description = "Enterprise piece-rate wages, shop-floor QC, anti-fraud validation and IoT edge capture for ERPNext"
app_email = "info@neotec.sa"
app_license = "Proprietary"
app_logo_url = "/assets/alphax_piecework/images/logo.svg"

required_apps = ["erpnext"]

add_to_apps_screen = [
	{
		"name": "alphax_piecework",
		"logo": "/assets/alphax_piecework/images/logo.svg",
		"title": "AlphaX PieceWork",
		"route": "/app/alphax-piecework",
	}
]

after_install = "alphax_piecework.setup.install.after_install"
after_migrate = "alphax_piecework.setup.install.after_migrate"
before_uninstall = "alphax_piecework.setup.install.before_uninstall"

doc_events = {
	"Job Card": {
		"on_submit": "alphax_piecework.integrations.job_card.on_submit",
		"on_cancel": "alphax_piecework.integrations.job_card.on_cancel",
	},
}

override_doctype_dashboards = {
	"Work Order": "alphax_piecework.overrides.dashboards.work_order",
	"Employee": "alphax_piecework.overrides.dashboards.employee",
}

scheduler_events = {
	"hourly": ["alphax_piecework.tasks.hourly"],
	"daily": ["alphax_piecework.tasks.daily"],
}

# Audit Events must survive when a referenced document is deleted.
ignore_links_on_delete = ["PieceWork Audit Event", "PieceWork Edge Event"]
