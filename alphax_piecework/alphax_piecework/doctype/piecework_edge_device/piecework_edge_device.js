frappe.ui.form.on("PieceWork Edge Device", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("API Usage"), () => {
			const base = window.location.origin;
			const sample = JSON.stringify({
				device_id: frm.doc.device_id,
				events: [{
					client_uuid: "8f0c6f0e-3b2a-4c1e-9d0a-000000000001",
					employee: "HR-EMP-00001",
					work_order: "MFG-WO-2026-00001",
					operation: frm.doc.default_operation || "Stitching",
					qty: 25,
					from_time: "2026-09-11 08:00:00",
					to_time: "2026-09-11 09:00:00",
				}],
			}, null, 2);
			frappe.msgprint({
				title: __("Edge API"),
				wide: true,
				message: `<p>${__("Authenticate as the API User with token auth. Replay-safe: resend the same client_uuid until acknowledged.")}</p>
<pre>curl -X POST ${base}/api/method/alphax_piecework.api.edge.ingest \\
  -H "Authorization: token API_KEY:API_SECRET" \\
  -H "Content-Type: application/json" \\
  -d '${frappe.utils.escape_html(sample)}'</pre>
<p>${__("Other endpoints")}: <code>alphax_piecework.api.edge.handshake</code>, <code>alphax_piecework.api.edge.work_orders</code></p>`,
			});
		});
	},
});
