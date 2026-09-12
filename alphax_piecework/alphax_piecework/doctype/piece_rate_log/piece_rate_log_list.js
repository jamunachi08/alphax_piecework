frappe.listview_settings["Piece Rate Log"] = {
	add_fields: ["review_status", "payroll_status", "capacity_status"],
	get_indicator(doc) {
		if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
		if (doc.docstatus === 0) return [__("Draft"), "grey", "docstatus,=,0"];
		if (doc.review_status === "Pending Review") return [__("Pending Review"), "orange", "review_status,=,Pending Review"];
		if (doc.review_status === "Rejected") return [__("Rejected"), "red", "review_status,=,Rejected"];
		if (doc.payroll_status === "Posted") return [__("Paid"), "green", "payroll_status,=,Posted"];
		if (doc.payroll_status === "Batched") return [__("Batched"), "blue", "payroll_status,=,Batched"];
		return [__("Unbatched"), "yellow", "payroll_status,=,Unbatched"];
	},
};
