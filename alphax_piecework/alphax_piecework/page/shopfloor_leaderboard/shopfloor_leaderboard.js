// AlphaX PieceWork - Shop Floor Leaderboard (overhead monitor view, EN/AR)
frappe.pages["shopfloor-leaderboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Shop Floor Leaderboard"), single_column: true });
	new APWLeaderboard(page, wrapper);
};

const APW_I18N = {
	en: { pieces: "Pieces Today", eff: "Efficiency", quality: "Quality", logs: "Logs", top: "Top Performers", hourly: "Output by Hour",
		cells: "Lines / Cells", rank: "#", name: "Operator", updated: "Updated", noData: "No production logged yet today" },
	ar: { pieces: "قطع اليوم", eff: "الكفاءة", quality: "الجودة", logs: "السجلات", top: "الأفضل أداءً", hourly: "الإنتاج بالساعة",
		cells: "الخطوط / الخلايا", rank: "#", name: "المشغل", updated: "آخر تحديث", noData: "لا يوجد إنتاج مسجل اليوم" },
};

class APWLeaderboard {
	constructor(page, wrapper) {
		this.page = page;
		this.lang = frappe.boot.lang === "ar" ? "ar" : "en";
		this.$root = $('<div class="apw-board"></div>').appendTo(page.main);
		this.company = page.add_field({ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"), change: () => this.load() });
		this.workstation = page.add_field({ fieldname: "workstation", label: __("Workstation"), fieldtype: "Link",
			options: "Workstation", change: () => this.load() });
		page.set_primary_action(__("Full Screen"), () => this.fullscreen(), "maximize");
		page.set_secondary_action("EN / عربي", () => { this.lang = this.lang === "en" ? "ar" : "en"; this.render(this.data); });
		this.load();
		$(wrapper).on("show", () => this.load());
	}
	t(key) { return APW_I18N[this.lang][key]; }
	fullscreen() { const el = this.$root.get(0); (el.requestFullscreen || el.webkitRequestFullscreen || (() => {})).call(el); }
	load() {
		clearTimeout(this.timer);
		frappe.xcall("alphax_piecework.api.dashboard.get_leaderboard", {
			company: this.company.get_value(), workstation: this.workstation.get_value(),
		}).then((data) => {
			this.data = data;
			this.render(data);
			this.timer = setTimeout(() => this.load(), (data.refresh_seconds || 30) * 1000);
		}).catch(() => { this.timer = setTimeout(() => this.load(), 60000); });
	}
	kpi(label, value, suffix, tone) {
		return `<div class="apw-kpi apw-${tone}"><div class="apw-kpi-label">${label}</div><div class="apw-kpi-value">${value}<small>${suffix || ""}</small></div></div>`;
	}
	hourlySvg(hourly) {
		if (!hourly.length) return "";
		const w = 560, h = 180, pad = 24, max = Math.max(...hourly.map((x) => x.qty), 1);
		const bw = (w - pad * 2) / Math.max(hourly.length, 1);
		const bars = hourly.map((x, i) => {
			const bh = (x.qty / max) * (h - pad * 2);
			const xPos = pad + i * bw + bw * 0.15;
			return `<rect x="${xPos}" y="${h - pad - bh}" width="${bw * 0.7}" height="${bh}" rx="4" class="apw-bar"/>
				<text x="${xPos + bw * 0.35}" y="${h - 6}" class="apw-axis">${String(x.hour).padStart(2, "0")}</text>
				<text x="${xPos + bw * 0.35}" y="${h - pad - bh - 6}" class="apw-bar-label">${Math.round(x.qty)}</text>`;
		}).join("");
		return `<svg viewBox="0 0 ${w} ${h}" class="apw-svg" role="img">${bars}</svg>`;
	}
	render(data) {
		if (!data) return;
		const dir = this.lang === "ar" ? "rtl" : "ltr";
		const tone = (v) => (v >= 85 ? "good" : v >= 60 ? "warn" : "bad");
		const rows = data.board.map((r) => `
			<tr class="${r.rank <= 3 ? "apw-podium apw-rank-" + r.rank : ""}">
				<td class="apw-rank">${r.rank}</td><td>${frappe.utils.escape_html(r.label || "")}</td>
				<td class="apw-num">${format_number(r.pieces, null, 0)}</td>
				<td class="apw-num apw-${tone(r.efficiency)}">${r.efficiency}%</td>
				<td class="apw-num apw-${tone(r.quality)}">${r.quality}%</td>
			</tr>`).join("");
		const cells = (data.cells || []).map((c) => `<div class="apw-cell"><span>${frappe.utils.escape_html(c.label)}</span><b>${format_number(c.pieces, null, 0)}</b></div>`).join("");
		this.$root.attr("dir", dir).html(`
			<div class="apw-kpis">
				${this.kpi(this.t("pieces"), format_number(data.totals.pieces, null, 0), "", "neutral")}
				${this.kpi(this.t("eff"), data.totals.efficiency, "%", tone(data.totals.efficiency))}
				${this.kpi(this.t("quality"), data.totals.quality, "%", tone(data.totals.quality))}
				${this.kpi(this.t("logs"), data.totals.logs, "", "neutral")}
			</div>
			<div class="apw-grid">
				<div class="apw-panel"><h3>${this.t("top")}</h3>
					${rows ? `<table class="apw-table"><thead><tr><th>${this.t("rank")}</th><th>${this.t("name")}</th><th>${this.t("pieces")}</th><th>${this.t("eff")}</th><th>${this.t("quality")}</th></tr></thead><tbody>${rows}</tbody></table>`
						: `<div class="apw-empty">${this.t("noData")}</div>`}
				</div>
				<div class="apw-side">
					<div class="apw-panel"><h3>${this.t("hourly")}</h3>${this.hourlySvg(data.hourly || [])}</div>
					<div class="apw-panel"><h3>${this.t("cells")}</h3>${cells || "—"}</div>
				</div>
			</div>
			<div class="apw-footer">${this.t("updated")}: ${frappe.datetime.now_time()} · ${data.date}</div>`);
	}
}
