frappe.listview_settings["PieceWork Audit Event"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Verify Hash Chain"), () => {
			frappe.call("alphax_piecework.engine.audit.verify_chain").then((r) => {
				const v = r.message;
				frappe.msgprint(v.ok
					? __("Chain intact: {0} events verified.", [v.checked])
					: __("Chain BROKEN at sequence {0} (after {1} valid events).", [v.broken_at, v.checked]));
			});
		});
	},
};
