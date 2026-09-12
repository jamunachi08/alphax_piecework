frappe.query_reports["Labor Effectiveness"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "group_by", label: __("Group By"), fieldtype: "Select", options: "Employee\nWorkstation\nOperation", default: "Employee", reqd: 1 },
		{ fieldname: "workstation", label: __("Workstation"), fieldtype: "Link", options: "Workstation" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "ole" && data) {
			const color = data.ole >= 85 ? "green" : data.ole >= 60 ? "orange" : "red";
			value = `<span style="color:var(--${color}-600);font-weight:600">${value}</span>`;
		}
		return value;
	},
};
