// AlphaX PieceWork - Piece Rate Log
frappe.ui.form.on("Piece Rate Log", {
	setup(frm) {
		frm.set_query("work_order", () => ({
			filters: { docstatus: 1, company: frm.doc.company, status: ["not in", ["Stopped", "Closed", "Completed"]] },
		}));
		frm.set_query("operation", () =>
			frm.doc.work_order
				? { query: "alphax_piecework.api.queries.work_order_operations", filters: { work_order: frm.doc.work_order } }
				: {}
		);
		frm.set_query("production_cell", () => ({ filters: { company: frm.doc.company, is_active: 1 } }));
		frm.set_query("workstation", () => ({}));
	},

	refresh(frm) {
		const colors = { "Pending Review": "orange", Approved: "green", Rejected: "red" };
		if (frm.doc.docstatus === 1 && colors[frm.doc.review_status]) {
			frm.dashboard.set_headline_alert(
				`<div class="text-${colors[frm.doc.review_status]}">${__("Capacity Review")}: ${__(frm.doc.review_status)}</div>`
			);
		}
		if (frm.doc.docstatus === 1 && frm.doc.review_status === "Pending Review" && frappe.user.has_role("PieceWork Manager")) {
			["Approved", "Rejected"].forEach((decision) => {
				frm.add_custom_button(__(decision === "Approved" ? "Approve Override" : "Reject Log"), () => {
					frappe.prompt(
						[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
						(v) =>
							frappe
								.call("alphax_piecework.alphax_piecework.doctype.piece_rate_log.piece_rate_log.review_log", {
									name: frm.doc.name,
									decision,
									reason: v.reason,
								})
								.then(() => frm.reload_doc()),
						__(decision === "Approved" ? "Approve Override" : "Reject Log")
					);
				}, __("Review"));
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.review_status !== "Rejected") {
			frm.add_custom_button(__("Record QC Failure"), () => {
				frappe.new_doc("Shop Floor QA Log", { piece_rate_log: frm.doc.name });
			});
		}
		if (frm.doc.docstatus === 0 && frm.doc.operation && frm.doc.company) {
			frm.add_custom_button(__("Preview Rate"), () => {
				frappe
					.call("alphax_piecework.engine.rates.preview_rate", {
						company: frm.doc.company,
						operation: frm.doc.operation,
						posting_date: frm.doc.posting_date,
						item_code: frm.doc.item_code,
						workstation_type: frm.doc.workstation_type,
						difficulty_class: frm.doc.difficulty_class,
					})
					.then((r) => {
						const m = r.message || {};
						frappe.msgprint(
							m.name
								? `${__("Matrix")}: <b>${m.name}</b><br>${__("Rate")}: ${format_currency(m.standard_rate, m.currency)}<br>SAM: ${m.sam_minutes || "-"}`
								: m.message
						);
					});
			});
		}
	},

	from_time: (frm) => frm.trigger("calc_hours"),
	to_time: (frm) => frm.trigger("calc_hours"),
	calc_hours(frm) {
		if (frm.doc.from_time && frm.doc.to_time) {
			const hrs = moment(frm.doc.to_time).diff(moment(frm.doc.from_time), "seconds") / 3600;
			frm.set_value("hours_worked", hrs > 0 ? flt(hrs, 2) : 0);
		}
	},
	worker_type(frm) {
		if (frm.doc.worker_type === "Individual") frm.clear_table("splits");
		frm.refresh_fields();
	},
	production_cell(frm) {
		frm.clear_table("splits");
		frm.refresh_field("splits");
	},
});
