// AlphaX PieceWork — interactive process flow board.
// Every node is a live stage: click it to see its records, create the next
// transaction, or open the stage report. Bilingual EN/AR.

frappe.pages["piecework-process-flow"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("PieceWork Process Flow"),
		single_column: true,
	});
	wrapper.apf = new APWFlowBoard(page, wrapper);
};

frappe.pages["piecework-process-flow"].on_page_show = function (wrapper) {
	if (wrapper.apf) wrapper.apf.load();
};

const APF_T = {
	en: {
		office: "Plant view — open work per stage",
		project: "Period view",
		pick: "Select a payroll period",
		all: "All open work",
		progress: "Flow progress",
		current: "Currently at",
		outputs: "Key Outputs at Each Stage",
		note: "The process may vary based on project type, client requirements, and contract terms.",
		records: "Records",
		drafts: "Drafts",
		newDoc: "New",
		openList: "Open List",
		report: "Stage Report",
		none: "Nothing recorded at this stage yet",
		done: "Done", pending: "Not started", blocked: "Waiting on the previous stage", currentState: "In progress",
		start: "Start", end: "End", legend: "Legend", refresh: "Refresh", fullscreen: "Full Screen",
		noAccess: "You do not have access to this stage.",
		subtitle: "From rate setup to posted wages",
	},
	ar: {
		office: "عرض المصنع — الأعمال المفتوحة لكل مرحلة",
		project: "عرض الفترة",
		pick: "اختر فترة الأجور",
		all: "جميع الأعمال المفتوحة",
		progress: "تقدم المسار",
		current: "المرحلة الحالية",
		outputs: "المخرجات الرئيسية لكل مرحلة",
		note: "قد تختلف الإجراءات حسب نوع المشروع ومتطلبات العميل وشروط العقد.",
		records: "السجلات",
		drafts: "المسودات",
		newDoc: "جديد",
		openList: "عرض القائمة",
		report: "تقرير المرحلة",
		none: "لا توجد سجلات في هذه المرحلة",
		done: "مكتملة", pending: "لم تبدأ", blocked: "بانتظار المرحلة السابقة", currentState: "قيد التنفيذ",
		start: "البداية", end: "النهاية", legend: "المفتاح", refresh: "تحديث", fullscreen: "ملء الشاشة",
		noAccess: "لا تملك صلاحية لهذه المرحلة.",
		subtitle: "من إعداد الأسعار حتى ترحيل الأجور",
	},
};

// Geometry of the chart, in SVG units.
const G = {
	laneW: 236, laneGap: 10, headerH: 74, top: 96, rowH: 86,
	boxW: 196, boxH: 50, decW: 150, decH: 74, termW: 118, termH: 38,
};

class APWFlowBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.lang = frappe.boot.lang === "ar" ? "ar" : "en";
		this.selection = {};
		this.$root = $('<div class="apf-board"></div>').appendTo(page.main);

		this.instanceField = page.add_field({
			fieldname: "instance", label: __("Payroll Period"), fieldtype: "Autocomplete", options: [],
			change: () => this.onSelect(),
		});
		page.add_field({
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: (e) => { this.company = $(e.target).val(); this.loadInstances().then(() => this.load()); },
		});
		this.company = frappe.defaults.get_user_default("Company");

		page.set_primary_action(__("New Wage Batch"), () => frappe.new_doc("PieceWork Wage Batch"), "add");
		page.add_menu_item(__("Refresh"), () => this.load());
		page.add_menu_item(__("Full Screen"), () => this.fullscreen());
		page.add_menu_item("EN / عربي", () => { this.lang = this.lang === "en" ? "ar" : "en"; this.render(); });
		page.add_menu_item(__("PieceWork Settings"), () => frappe.set_route("Form", "PieceWork Settings"));
		page.add_menu_item(__("Leaderboard"), () => frappe.set_route("shopfloor-leaderboard"));

		this.$root.on("click", "[data-node]", (e) => this.openNode($(e.currentTarget).attr("data-node")));
		this.$root.on("click", ".apf-instance-row", (e) => {
			const key = $(e.currentTarget).attr("data-key");
			this.instanceField.set_value(key);
		});

		this.loadInstances().then(() => this.load());
	}

	t(key) { return (APF_T[this.lang] || APF_T.en)[key]; }

	fullscreen() {
		const el = this.$root.get(0);
		(el.requestFullscreen || el.webkitRequestFullscreen || (() => {})).call(el);
	}

	loadInstances() {
		return frappe.xcall("alphax_piecework.api.board.get_flow_instances", { company: this.company })
			.then((rows) => {
				this.instances = rows || [];
				this.instanceField.df.options = this.instances.map((r) => ({
					value: r.key || "__open__",
					label: r.title,
					description: `${r.status || ""} · ${r.current_label || ""}`.trim(),
				}));
				this.instanceField.set_data(this.instanceField.df.options);
			})
			.catch(() => { this.instances = []; });
	}

	onSelect() {
		const key = this.instanceField.get_value();
		const row = (this.instances || []).find((r) => (r.key || "__open__") === key);
		if (!row) this.selection = {};
		else if (row.type === "Batch") this.selection = { batch: row.key };
		else this.selection = { from_date: row.from_date, to_date: row.to_date };
		this.load();
	}

	load() {
		clearTimeout(this.timer);
		const args = Object.assign({ company: this.company }, this.selection);
		return frappe.xcall("alphax_piecework.api.board.get_board", args)
			.then((data) => {
				this.data = data;
				this.render();
				this.timer = setTimeout(() => this.load(), (data.refresh_seconds || 120) * 1000);
			})
			.catch((e) => {
				this.$root.html(`<div class="apf-error">${frappe.utils.escape_html(e.message || "Failed to load the board")}</div>`);
			});
	}

	// ---------------------------------------------------------------- geometry
	// Title and subtitle lines decide the box height, so edges know where boxes really end.
	lines(node) {
		const label = this.lang === "ar" ? (node.ar || node.en) : node.en;
		return {
			title: this.wrapText((node.step ? node.step + ". " : "") + label, 24),
			sub: node.sub ? this.wrapText(node.sub, 30).slice(0, 2) : [],
		};
	}

	pos(node) {
		const laneIdx = this.data.lanes.findIndex((l) => l.id === node.lane);
		const cx = laneIdx * (G.laneW + G.laneGap) + G.laneW / 2;
		const cy = G.top + node.row * G.rowH + G.rowH / 2;
		let w = G.boxW, h = G.boxH;
		if (node.kind === "decision") { w = G.decW; h = G.decH; }
		else if (node.kind === "terminator") { w = G.termW; h = G.termH; }
		else {
			const l = this.lines(node);
			h = Math.max(G.boxH, 20 + l.title.length * 13 + l.sub.length * 11);
		}
		return { cx, cy, w, h, x: cx - w / 2, y: cy - h / 2 };
	}

	stateColor(state) {
		return {
			done: { fill: "#DCFCE7", stroke: "#16A34A", text: "#14532D" },
			current: { fill: "#FEF9C3", stroke: "#CA8A04", text: "#713F12" },
			blocked: { fill: "#FEE2E2", stroke: "#DC2626", text: "#7F1D1D" },
			pending: { fill: "#FFFFFF", stroke: "#94A3B8", text: "#475569" },
		}[state] || { fill: "#FFFFFF", stroke: "#94A3B8", text: "#475569" };
	}

	wrapText(text, maxChars) {
		const words = String(text || "").split(" ");
		const lines = [];
		let line = "";
		words.forEach((w) => {
			if ((line + " " + w).trim().length > maxChars) { if (line) lines.push(line); line = w; }
			else line = (line + " " + w).trim();
		});
		if (line) lines.push(line);
		return lines.slice(0, 3);
	}

	// ---------------------------------------------------------------- svg
	svg() {
		const d = this.data;
		const width = d.lanes.length * (G.laneW + G.laneGap);
		const maxRow = Math.max(...d.nodes.map((n) => n.row));
		const height = G.top + (maxRow + 1) * G.rowH + 24;
		const esc = frappe.utils.escape_html;

		const lanes = d.lanes.map((lane, i) => {
			const x = i * (G.laneW + G.laneGap);
			const label = this.wrapText(this.lang === "ar" ? lane.ar : lane.en, 24);
			const title = label.map((l, li) =>
				`<text x="${x + G.laneW / 2}" y="${44 + li * 15}" class="apf-lane-title">${esc(l)}</text>`).join("");
			return `<rect x="${x}" y="0" width="${G.laneW}" height="${G.headerH}" rx="6" fill="${lane.fill}" stroke="${lane.stroke}" stroke-width="1"/>
				<rect x="${x}" y="${G.headerH}" width="${G.laneW}" height="${height - G.headerH}" fill="${lane.fill}" opacity="0.18"/>
				<circle cx="${x + G.laneW / 2}" cy="22" r="9" fill="${lane.stroke}" opacity="0.85"/>
				${title}`;
		}).join("");

		const edges = d.edges.map((e) => this.edgePath(e)).join("");

		const nodes = d.nodes.map((n) => {
			const p = this.pos(n);
			const c = this.stateColor(n.state);
			const label = this.lang === "ar" ? (n.ar || n.en) : n.en;
			const clickable = n.doctype ? "apf-clickable" : "";
			const tip = `${label}${n.caption ? " · " + n.caption : ""}`;

			if (n.kind === "terminator") {
				const fill = n.id === "start" ? "#16794D" : "#B91C1C";
				return `<g data-node="${n.id}" class="apf-node"><title>${esc(tip)}</title>
					<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="${p.h / 2}" fill="${fill}"/>
					<text x="${p.cx}" y="${p.cy + 5}" class="apf-term-label">${esc(this.t(n.id))}</text></g>`;
			}
			if (n.kind === "decision") {
				const lines = this.wrapText(label, 14);
				const pts = `${p.cx},${p.y} ${p.x + p.w},${p.cy} ${p.cx},${p.y + p.h} ${p.x},${p.cy}`;
				return `<g data-node="${n.id}" class="apf-node ${clickable}"><title>${esc(tip)}</title>
					<polygon points="${pts}" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.6"/>
					${lines.map((l, i) => `<text x="${p.cx}" y="${p.cy - (lines.length - 1) * 6 + i * 12 + 4}" class="apf-dec-label" fill="${c.text}">${esc(l)}</text>`).join("")}
					</g>`;
			}

			const { title: titleLines, sub: subLines } = this.lines(n);
			const boxH = p.h;
			const y = p.y;
			const badge = n.count
				? `<g><circle cx="${p.x + p.w - 13}" cy="${y + 13}" r="11" fill="${c.stroke}"/>
					<text x="${p.x + p.w - 13}" y="${y + 17}" class="apf-badge">${n.count > 99 ? "99+" : n.count}</text></g>`
				: "";
			const tick = n.state === "done"
				? `<path d="M${p.x + 8} ${y + boxH - 12} l4 4 l7 -9" stroke="#16A34A" stroke-width="2.4" fill="none" stroke-linecap="round"/>` : "";
			const pulse = n.state === "current"
				? `<rect x="${p.x - 3}" y="${y - 3}" width="${p.w + 6}" height="${boxH + 6}" rx="9" fill="none" stroke="${c.stroke}" stroke-width="2" opacity="0.45" class="apf-pulse"/>` : "";
			const dash = n.loop ? ' stroke-dasharray="5 3"' : "";

			return `<g data-node="${n.id}" class="apf-node ${clickable}"><title>${esc(tip)}</title>
				${pulse}
				<rect x="${p.x}" y="${y}" width="${p.w}" height="${boxH}" rx="7" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.6"${dash}/>
				${titleLines.map((l, i) => `<text x="${p.cx}" y="${y + 17 + i * 13}" class="apf-node-label" fill="${c.text}">${esc(l)}</text>`).join("")}
				${subLines.map((l, i) => `<text x="${p.cx}" y="${y + 19 + titleLines.length * 13 + i * 11}" class="apf-node-sub">${esc(l)}</text>`).join("")}
				${badge}${tick}</g>`;
		}).join("");

		return `<svg viewBox="0 0 ${width} ${height}" class="apf-svg" xmlns="http://www.w3.org/2000/svg">
			<defs><marker id="apfArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
				<path d="M0,0 L10,5 L0,10 z" fill="#475569"/></marker></defs>
			${lanes}${edges}${nodes}</svg>`;
	}

	edgePath(edge) {
		const from = this.data.nodes.find((n) => n.id === edge.from);
		const to = this.data.nodes.find((n) => n.id === edge.to);
		if (!from || !to) return "";
		const a = this.pos(from), b = this.pos(to);
		const loop = edge.style === "loop";
		const cls = loop ? "apf-edge apf-edge-loop" : "apf-edge";
		let d;
		if (Math.abs(a.cx - b.cx) < 2) {
			d = `M${a.cx} ${a.cy + a.h / 2} L${b.cx} ${b.cy - b.h / 2}`;
		} else if (Math.abs(a.cy - b.cy) < 2) {
			const dir = b.cx > a.cx ? 1 : -1;
			d = `M${a.cx + dir * a.w / 2} ${a.cy} L${b.cx - dir * b.w / 2} ${b.cy}`;
		} else if (loop) {
			const dir = b.cx > a.cx ? 1 : -1;
			const sx = a.cx + dir * a.w / 2;
			const mx = sx + dir * 26;
			d = `M${sx} ${a.cy} L${mx} ${a.cy} L${mx} ${b.cy} L${b.cx - dir * b.w / 2} ${b.cy}`;
		} else {
			const sy = a.cy + a.h / 2;
			const my = sy + (b.cy - b.h / 2 - sy) / 2;
			d = `M${a.cx} ${sy} L${a.cx} ${my} L${b.cx} ${my} L${b.cx} ${b.cy - b.h / 2}`;
		}
		let label = "";
		if (edge.label) {
			// On a vertical exit the midpoint sits under the diamond, so nudge the label aside.
			const vertical = Math.abs(a.cx - b.cx) < 2;
			const lx = vertical ? a.cx + 16 : (a.cx + b.cx) / 2;
			const ly = vertical ? a.cy + a.h / 2 + 14 : (a.cy + b.cy) / 2 - 6;
			label = `<text x="${lx}" y="${ly}" class="apf-edge-label">${frappe.utils.escape_html(this.lang === "ar" ? edge.ar : edge.label)}</text>`;
		}
		return `<path d="${d}" class="${cls}" marker-end="url(#apfArrow)"/>${label}`;
	}

	// ---------------------------------------------------------------- render
	render() {
		if (!this.data) return;
		const d = this.data;
		const esc = frappe.utils.escape_html;
		const ctx = d.context || {};
		const currentNode = d.current ? d.nodes.find((n) => n.id === d.current) : null;
		const headline = d.mode === "period"
			? `<b>${esc(ctx.title || "")}</b>${ctx.status ? ` · ${esc(ctx.status)}` : ""}`
			: this.t("office");

		const statusStrip = d.mode === "period"
			? `<div class="apf-strip">
					<div class="apf-prog"><div class="apf-prog-bar" style="width:${d.progress}%"></div></div>
					<div class="apf-prog-text">${this.t("progress")}: <b>${d.progress}%</b>${currentNode
						? ` · ${this.t("current")}: <b>${esc(this.lang === "ar" ? (currentNode.ar || currentNode.en) : currentNode.en)}</b>` : ""}</div>
				</div>` : "";

		const instances = (this.instances || []).slice(0, 8).map((r) => `
			<div class="apf-instance-row" data-key="${esc(r.key || "__open__")}">
				<div><b>${esc(r.title || "")}</b><div class="apf-muted">${esc(r.status || "")}</div></div>
				<div class="apf-instance-right">
					<span class="apf-chip">${esc(r.current_label || r.status || "")}</span>
					<div class="apf-mini-prog"><span style="width:${r.progress}%"></span></div>
				</div>
			</div>`).join("");

		const outputs = d.key_outputs.map((o) => {
			const lane = d.lanes.find((l) => l.id === o.lane) || {};
			return `<div class="apf-output" style="background:${lane.fill};border-color:${lane.stroke}">
				<div class="apf-output-h"><span class="apf-output-n" style="background:${lane.stroke}">${o.n}</span>
					<b>${esc(this.lang === "ar" ? o.ar : o.en)}</b></div>
				<ul>${o.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`;
		}).join("");

		const legend = ["done", "current", "blocked", "pending"].map((s) => {
			const c = this.stateColor(s);
			const label = s === "current" ? this.t("currentState") : this.t(s);
			return `<span class="apf-legend-item"><i style="background:${c.fill};border-color:${c.stroke}"></i>${esc(label)}</span>`;
		}).join("");

		this.$root.attr("dir", this.lang === "ar" ? "rtl" : "ltr").html(`
			<div class="apf-head">
				<div>
					<div class="apf-title">${__("PieceWork Process Flow")}</div>
					<div class="apf-sub">${esc(this.t("subtitle"))}</div>
				</div>
				<div class="apf-head-right">${headline}</div>
			</div>
			${statusStrip}
			<div class="apf-main">
				<div class="apf-chart">${this.svg()}</div>
				<div class="apf-side">
					<div class="apf-card"><h4>${esc(this.t("pick"))}</h4>${instances || `<div class="apf-muted">${esc(this.t("none"))}</div>`}</div>
					<div class="apf-card"><h4>${esc(this.t("legend"))}</h4><div class="apf-legend">${legend}</div></div>
				</div>
			</div>
			<div class="apf-outputs-wrap">
				<h4>${esc(this.t("outputs"))}</h4>
				<div class="apf-outputs">${outputs}</div>
			</div>
			<div class="apf-note">${esc(this.t("note"))}</div>`);
	}

	// ---------------------------------------------------------------- node dialog
	openNode(nodeId) {
		const args = Object.assign({ node_id: nodeId, company: this.company }, this.selection);
		frappe.xcall("alphax_piecework.api.board.get_node_detail", args)
			.then((detail) => this.showNodeDialog(detail))
			.catch((e) => frappe.msgprint({ title: __("Stage"), indicator: "red",
				message: frappe.utils.escape_html(e.message || this.t("noAccess")) }));
	}

	showNodeDialog(detail) {
		const n = detail.node;
		const esc = frappe.utils.escape_html;
		const label = this.lang === "ar" ? (n.ar || n.en) : n.en;
		const rows = (detail.documents || []).map((doc) => {
			const status = doc.status || doc.client_decision || doc.client_review_status || doc.decision || "";
			return `<div class="apf-doc"><a href="/app/${frappe.router.slug(n.doctype)}/${encodeURIComponent(doc.name)}">${esc(doc.name)}</a>
				<span class="apf-chip">${esc(status)}</span></div>`;
		}).join("");
		const drafts = (detail.drafts || []).map((doc) =>
			`<div class="apf-doc"><a href="/app/${frappe.router.slug(n.doctype)}/${encodeURIComponent(doc.name)}">${esc(doc.name)}</a>
				<span class="apf-chip apf-chip-draft">${__("Draft")}</span></div>`).join("");

		const d = new frappe.ui.Dialog({
			title: (n.step ? n.step + ". " : "") + label,
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
		});
		d.fields_dict.body.$wrapper.html(`
			<div class="apf-dlg">
				${n.sub ? `<p class="apf-muted">${esc(n.sub)}</p>` : ""}
				${n.caption ? `<p><b>${esc(n.caption)}</b></p>` : ""}
				${n.output && n.output.length ? `<p class="apf-muted">${__("Outputs")}: ${n.output.map(esc).join(" · ")}</p>` : ""}
				<h5>${esc(this.t("records"))} (${detail.documents.length})</h5>
				${rows || `<div class="apf-muted">${esc(this.t("none"))}</div>`}
				${drafts ? `<h5>${esc(this.t("drafts"))}</h5>${drafts}` : ""}
			</div>`);

		if (n.doctype && n.can_create && detail.new_defaults) {
			d.set_primary_action(__("New {0}", [__(n.doctype)]), () => {
				d.hide();
				frappe.new_doc(n.doctype, detail.new_defaults);
			});
		}
		if (n.doctype && n.can_read) {
			d.set_secondary_action_label(__("Open List"));
			d.set_secondary_action(() => {
				d.hide();
				frappe.set_route("List", n.doctype, detail.list_filters || {});
			});
		}
		if (n.report) {
			d.$wrapper.find(".modal-footer").prepend(
				$(`<button class="btn btn-default btn-sm">${__("Stage Report")}</button>`).on("click", () => {
					d.hide();
					frappe.set_route("query-report", n.report, detail.report_filters || {});
				}));
		}
		d.show();
	}
}
