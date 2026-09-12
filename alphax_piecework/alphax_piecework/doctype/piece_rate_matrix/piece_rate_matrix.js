frappe.ui.form.on("Piece Rate Matrix", {
	company(frm) {
		if (frm.doc.company && !frm.doc.currency) {
			frappe.db.get_value("Company", frm.doc.company, "default_currency").then((r) => frm.set_value("currency", r.message.default_currency));
		}
	},
	refresh(frm) {
		frm.set_intro(
			frm.doc.docstatus === 0
				? __("Rates take effect only after submission. Changes to a submitted rate require Cancel → Amend with a reason (audited).")
				: ""
		);
	},
});
