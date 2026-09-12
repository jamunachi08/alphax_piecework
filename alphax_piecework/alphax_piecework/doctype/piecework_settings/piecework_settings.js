frappe.ui.form.on("PieceWork Settings", {
	setup(frm) {
		frm.set_query("wage_expense_account", "company_accounts", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { company: row.company, is_group: 0, root_type: "Expense" } };
		});
		frm.set_query("wage_payable_account", "company_accounts", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { company: row.company, is_group: 0, root_type: "Liability" } };
		});
		frm.set_query("default_cost_center", "company_accounts", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { company: row.company, is_group: 0 } };
		});
	},
	refresh(frm) {
		frm.add_custom_button(__("Shop-Floor Leaderboard"), () => frappe.set_route("shopfloor-leaderboard"));
		frm.add_custom_button(__("Operator Terminal"), () => frappe.set_route("piecework-terminal"));
	},
});
