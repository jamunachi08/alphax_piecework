// AlphaX PieceWork - Shop Floor QA Log
frappe.ui.form.on("Shop Floor QA Log", {
	setup(frm) {
		frm.set_query("piece_rate_log", () => ({
			filters: { docstatus: 1, review_status: ["!=", "Rejected"] },
		}));
		frm.set_query("defect_code", () => ({ filters: { is_active: 1 } }));
	},
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.total_worker_deduction) {
			frm.dashboard.add_indicator(
				__("Worker deduction {0}", [format_currency(frm.doc.total_worker_deduction, frm.doc.currency)]),
				"orange"
			);
		}
	},
	defect_code(frm) {
		if (!frm.doc.defect_code) return;
		frappe.db.get_value("PieceWork Defect Code", frm.doc.defect_code, ["default_disposition", "responsibility"]).then((r) => {
			const v = r.message || {};
			if (v.responsibility) frm.set_value("responsibility", v.responsibility);
			if (!frm.doc.disposition && v.default_disposition) frm.set_value("disposition", v.default_disposition);
		});
	},
	disposition(frm) {
		if (!frm.doc.disposition) return;
		frappe
			.call("alphax_piecework.alphax_piecework.doctype.shop_floor_qa_log.shop_floor_qa_log.get_percent_defaults", {
				disposition: frm.doc.disposition,
			})
			.then((r) => {
				Object.entries(r.message || {}).forEach(([k, v]) => frm.set_value(k, v));
				frm.set_value("defaults_applied", 1);
			});
	},
});
