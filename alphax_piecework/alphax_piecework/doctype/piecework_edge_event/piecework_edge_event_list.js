frappe.listview_settings["PieceWork Edge Event"] = {
	get_indicator(doc) {
		return { Queued: [__("Queued"), "blue", "status,=,Queued"], Processed: [__("Processed"), "green", "status,=,Processed"],
			Failed: [__("Failed"), "red", "status,=,Failed"] }[doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Retry Failed"), () =>
			frappe.call("alphax_piecework.engine.edge.retry_failed_events").then(() => listview.refresh()));
		listview.page.add_inner_button(__("Process Queue Now"), () =>
			frappe.call("alphax_piecework.api.edge.process_now").then(() => listview.refresh()));
	},
};
