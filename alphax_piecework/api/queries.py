import frappe


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def work_order_operations(doctype, txt, searchfield, start, page_len, filters):
	return frappe.db.sql(
		"""select distinct operation, workstation from `tabWork Order Operation`
		where parent = %(wo)s and parenttype = 'Work Order' and operation like %(txt)s
		order by idx limit %(start)s, %(page_len)s""",
		{"wo": filters.get("work_order"), "txt": f"%{txt}%", "start": start, "page_len": page_len},
	)
