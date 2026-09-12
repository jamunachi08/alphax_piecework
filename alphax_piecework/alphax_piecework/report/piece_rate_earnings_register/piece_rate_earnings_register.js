frappe.query_reports["Piece Rate Earnings Register"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "production_cell", label: __("Production Cell"), fieldtype: "Link", options: "Production Cell" },
		{ fieldname: "operation", label: __("Operation"), fieldtype: "Link", options: "Operation" },
		{ fieldname: "payroll_status", label: __("Payroll Status"), fieldtype: "Select", options: "\nUnbatched\nBatched\nPosted" },
	],
};
