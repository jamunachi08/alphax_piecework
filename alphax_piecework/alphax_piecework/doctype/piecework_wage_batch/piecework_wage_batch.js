// AlphaX PieceWork - Wage Batch
frappe.ui.form.on("PieceWork Wage Batch", {
	setup(frm) {
		frm.set_query("production_cell", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("department", () => ({ filters: { company: frm.doc.company } }));
	},
	refresh(frm) {
		const colors = { Queued: "blue", Posted: "green", "Partially Posted": "orange", Failed: "red" };
		if (frm.doc.docstatus === 1 && colors[frm.doc.status]) {
			frm.dashboard.set_headline_alert(`<div class="text-${colors[frm.doc.status]}">${__("Posting")}: ${__(frm.doc.status)}</div>`);
		}
		if (frm.doc.docstatus === 1 && ["Failed", "Partially Posted"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Retry Posting"), () => frm.call("retry_posting").then(() => frm.reload_doc()));
		}
		if (frm.doc.journal_entry) {
			frm.add_custom_button(__("Journal Entry"), () => frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry), __("View"));
		}
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Earnings Register"), () =>
				frappe.set_route("query-report", "Piece Rate Earnings Register", {
					company: frm.doc.company, from_date: frm.doc.from_date, to_date: frm.doc.to_date,
				}), __("View"));
		}
	},
	get_entries_button(frm) {
		if (frm.is_dirty() || frm.is_new()) {
			frm.save().then(() => frm.call("fetch_entries").then(() => frm.reload_doc()));
		} else {
			frm.call("fetch_entries").then(() => frm.reload_doc());
		}
	},
});
