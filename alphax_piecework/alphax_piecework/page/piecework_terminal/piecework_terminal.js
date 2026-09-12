// AlphaX PieceWork - Operator Terminal: offline-first capture with replay-safe sync.
frappe.pages["piecework-terminal"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("PieceWork Terminal"), single_column: true });
	new APWTerminal(page);
};

class APWTerminal {
	constructor(page) {
		this.page = page;
		this.QKEY = "apw_terminal_queue_v1";
		this.CKEY = "apw_terminal_config_v1";
		this.config = JSON.parse(localStorage.getItem(this.CKEY) || "{}");
		this.qty = 0;
		this.syncing = false;
		this.$root = $('<div class="apw-term"></div>').appendTo(page.main);
		page.set_secondary_action(__("Device Setup"), () => this.setup());
		page.set_primary_action(__("Sync Now"), () => this.sync(true), "refresh");
		this.render();
		if (!this.config.device_id) this.setup();
		else this.loadWorkOrders();
		window.addEventListener("online", () => this.sync());
		this.interval = setInterval(() => this.sync(), 15000);
	}

	queue() { return JSON.parse(localStorage.getItem(this.QKEY) || "[]"); }
	saveQueue(q) { localStorage.setItem(this.QKEY, JSON.stringify(q)); this.updateStatus(); }
	uuid() {
		if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
		return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
			const r = (Math.random() * 16) | 0; return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
		});
	}
	nowStr() { return frappe.datetime.now_datetime(); }

	setup() {
		const d = new frappe.ui.Dialog({
			title: __("Device Setup"),
			fields: [
				{ fieldname: "device_id", label: __("Edge Device"), fieldtype: "Link", options: "PieceWork Edge Device", reqd: 1, default: this.config.device_id },
			],
			primary_action: (v) => {
				this.config.device_id = v.device_id;
				localStorage.setItem(this.CKEY, JSON.stringify(this.config));
				d.hide();
				this.loadWorkOrders();
			},
		});
		d.show();
	}

	loadWorkOrders() {
		frappe.xcall("alphax_piecework.api.edge.work_orders", { device_id: this.config.device_id })
			.then((orders) => {
				this.orders = orders || [];
				localStorage.setItem("apw_terminal_orders_v1", JSON.stringify(this.orders));
				this.fillOrders();
			})
			.catch(() => {
				this.orders = JSON.parse(localStorage.getItem("apw_terminal_orders_v1") || "[]");
				this.fillOrders();
			});
	}

	render() {
		this.$root.html(`
			<div class="apw-term-status"><span class="apw-dot"></span><span class="apw-status-text"></span></div>
			<div class="apw-term-grid">
				<div class="apw-term-form">
					<div class="apw-f-employee"></div>
					<label>${__("Work Order")}</label><select class="form-control apw-wo"></select>
					<label>${__("Operation")}</label><select class="form-control apw-op"></select>
					<div class="apw-window">${__("Window starts")}: <b class="apw-start">—</b>
						<button class="btn btn-default btn-xs apw-restart">${__("Start Now")}</button></div>
				</div>
				<div class="apw-term-pad">
					<div class="apw-qty">0</div>
					<div class="apw-steps">
						${[1, 5, 10, 25].map((n) => `<button class="btn btn-lg apw-step" data-n="${n}">+${n}</button>`).join("")}
						<button class="btn btn-lg apw-step apw-minus" data-n="-1">−1</button>
						<button class="btn btn-lg apw-clear">C</button>
					</div>
					<button class="btn btn-primary btn-lg apw-log">${__("Log Pieces")}</button>
				</div>
			</div>
			<div class="apw-recent"></div>`);

		this.employee = frappe.ui.form.make_control({
			parent: this.$root.find(".apw-f-employee"),
			df: { fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee",
				change: () => this.updateWindow() },
			render_input: true,
		});
		this.$root.on("click", ".apw-step", (e) => { this.qty = Math.max(0, this.qty + parseInt($(e.currentTarget).data("n"))); this.showQty(); });
		this.$root.on("click", ".apw-clear", () => { this.qty = 0; this.showQty(); });
		this.$root.on("click", ".apw-log", () => this.log());
		this.$root.on("click", ".apw-restart", () => this.setWindowStart(this.nowStr()));
		this.$root.on("change", ".apw-wo", () => this.fillOperations());
		this.updateStatus();
	}

	fillOrders() {
		const $wo = this.$root.find(".apw-wo").empty().append(`<option value="">${__("(none)")}</option>`);
		(this.orders || []).forEach((o) => $wo.append(`<option value="${o.name}">${o.name} · ${frappe.utils.escape_html(o.item_name || o.production_item)} (${o.qty})</option>`));
		this.fillOperations();
	}
	fillOperations() {
		const wo = (this.orders || []).find((o) => o.name === this.$root.find(".apw-wo").val());
		const $op = this.$root.find(".apw-op").empty();
		(wo ? wo.operations : []).forEach((op) => $op.append(`<option value="${op.operation}">${frappe.utils.escape_html(op.operation)}</option>`));
		if (!wo) $op.append(`<option value="">${__("Device default")}</option>`);
	}

	windowKey() { return `apw_window_${this.employee.get_value() || "none"}`; }
	setWindowStart(ts) { localStorage.setItem(this.windowKey(), ts); this.updateWindow(); }
	updateWindow() { this.$root.find(".apw-start").text(localStorage.getItem(this.windowKey()) || "—"); }
	showQty() { this.$root.find(".apw-qty").text(this.qty); }

	log() {
		const employee = this.employee.get_value();
		if (!employee) return frappe.show_alert({ message: __("Select an employee"), indicator: "orange" });
		if (!this.qty) return frappe.show_alert({ message: __("Enter a quantity"), indicator: "orange" });
		const to = this.nowStr();
		const from = localStorage.getItem(this.windowKey()) || to;
		const event = {
			client_uuid: this.uuid(), employee, qty: this.qty, from_time: from, to_time: to,
			work_order: this.$root.find(".apw-wo").val() || null, operation: this.$root.find(".apw-op").val() || null,
		};
		const q = this.queue(); q.push(event); this.saveQueue(q);
		this.setWindowStart(to);
		this.$root.find(".apw-recent").prepend(`<div class="apw-recent-row">${to.slice(11, 16)} · ${frappe.utils.escape_html(employee)} · <b>${this.qty}</b></div>`);
		this.qty = 0; this.showQty();
		frappe.utils.play_sound("click");
		this.sync();
	}

	updateStatus() {
		const pending = this.queue().length;
		const online = navigator.onLine;
		this.$root.find(".apw-dot").toggleClass("apw-offline", !online);
		this.$root.find(".apw-status-text").text(
			`${online ? __("Online") : __("Offline")} · ${__("Pending")}: ${pending}` + (this.config.device_id ? ` · ${this.config.device_id}` : "")
		);
	}

	async sync(manual) {
		const q = this.queue();
		if (this.syncing || !q.length || !this.config.device_id || !navigator.onLine) { this.updateStatus(); return; }
		this.syncing = true;
		try {
			const batch = q.slice(0, 200);
			const res = await fetch("/api/method/alphax_piecework.api.edge.ingest", {
				method: "POST",
				headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token },
				body: JSON.stringify({ device_id: this.config.device_id, events: batch }),
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const msg = (await res.json()).message || {};
			const done = new Set([...(msg.accepted || []), ...(msg.duplicates || [])]);
			const rejected = msg.rejected || [];
			rejected.forEach((r) => r.client_uuid && done.add(r.client_uuid));
			this.saveQueue(this.queue().filter((e) => !done.has(e.client_uuid)));
			if (rejected.length) {
				frappe.msgprint({ title: __("Rejected by server"), indicator: "red",
					message: rejected.map((r) => `${r.client_uuid}: ${frappe.utils.escape_html(r.error)}`).join("<br>") });
			}
			if (manual) frappe.show_alert({ message: __("Synced {0} event(s)", [done.size]), indicator: "green" });
		} catch (e) {
			if (manual) frappe.show_alert({ message: __("Sync failed, will retry: {0}", [e.message]), indicator: "orange" });
		} finally {
			this.syncing = false;
			this.updateStatus();
		}
	}
}
