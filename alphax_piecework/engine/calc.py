"""AlphaX PieceWork - pure calculation engine.

Deliberately free of any Frappe import so every money / capacity formula can be
unit-tested in isolation (see alphax_piecework/tests/test_calc.py) and reused by
controllers, background jobs and reports without drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

CAPACITY_RATED = "Workstation Rated Speed"
CAPACITY_SAM = "Standard Allowed Minutes (SAM)"
CAPACITY_STRICTER = "Stricter of Both"

SPLIT_EQUAL = "Equal"
SPLIT_HOURS = "By Hours Worked"
SPLIT_SKILL = "By Skill Weight"
SPLIT_HOURS_SKILL = "By Hours x Skill Weight"

DISP_SCRAP = "Scrap"
DISP_REWORK_ORIGINAL = "Rework - Original Worker"
DISP_REWORK_THIRD = "Rework - Third Party"


def r(value, precision: int = 2) -> float:
	"""Commercial (half-up) rounding; float() round() is banker's rounding."""
	if value is None:
		return 0.0
	q = Decimal(1).scaleb(-precision)
	return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def f(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def hours_between(from_time: datetime | None, to_time: datetime | None) -> float:
	if not from_time or not to_time:
		return 0.0
	return r((to_time - from_time).total_seconds() / 3600.0, 4)


# ---------------------------------------------------------------- capacity
def capacity_limit(
	hours: float,
	rated_pieces_per_hour: float = 0,
	sam_minutes: float = 0,
	max_efficiency_percent: float = 130,
	basis: str = CAPACITY_STRICTER,
	skill_factor_percent: float = 100,
):
	"""Return (limit, basis_used). limit is None when no basis is configured.

	Rated speed : hours x machine max pieces/hour
	SAM         : (hours x 60 / SAM) x max efficiency %  (apparel/electronics standard)
	Stricter    : min() of whichever are configured
	Skill factor scales the ceiling down for trainees (e.g. 70 %).
	"""
	hours = f(hours)
	candidates = {}
	if basis in (CAPACITY_RATED, CAPACITY_STRICTER) and f(rated_pieces_per_hour) > 0:
		candidates[CAPACITY_RATED] = hours * f(rated_pieces_per_hour)
	if basis in (CAPACITY_SAM, CAPACITY_STRICTER) and f(sam_minutes) > 0:
		candidates[CAPACITY_SAM] = (hours * 60.0 / f(sam_minutes)) * (f(max_efficiency_percent) or 100) / 100.0
	if not candidates:
		return None, None
	basis_used = min(candidates, key=candidates.get)
	factor = (f(skill_factor_percent) or 100) / 100.0
	return r(candidates[basis_used] * factor, 2), basis_used


# ---------------------------------------------------------------- earnings
@dataclass
class Earnings:
	regular_amount: float
	overtime_amount: float
	premium_amount: float
	gross_amount: float
	overtime_pieces: float


def compute_earnings(
	qty: float,
	rate: float,
	hours: float = 0,
	overtime_hours: float = 0,
	overtime_multiplier: float = 1.5,
	urgent_premium_percent: float = 0,
	multi_skill_bonus_percent: float = 0,
) -> Earnings:
	"""Overtime pieces are apportioned by hours (pieces x OT hours / total hours)."""
	qty, rate, hours, ot_hours = f(qty), f(rate), f(hours), f(overtime_hours)
	ot_pieces = 0.0
	if hours > 0 and ot_hours > 0:
		ot_pieces = qty * min(ot_hours, hours) / hours
	regular = (qty - ot_pieces) * rate
	overtime = ot_pieces * rate * (f(overtime_multiplier) or 1)
	premium = (regular + overtime) * (f(urgent_premium_percent) + f(multi_skill_bonus_percent)) / 100.0
	regular, overtime, premium = r(regular), r(overtime), r(premium)
	return Earnings(regular, overtime, premium, r(regular + overtime + premium), r(ot_pieces, 4))


def specificity_score(item_code=None, workstation_type=None, difficulty_class=None, item_group=None) -> int:
	"""Most specific Piece Rate Matrix row wins. Item > Workstation Type > Difficulty > Item Group."""
	return (8 if item_code else 0) + (4 if workstation_type else 0) + (2 if difficulty_class else 0) + (1 if item_group else 0)


# ---------------------------------------------------------------- team split
def split_amount(total: float, members: list[dict], method: str = SPLIT_HOURS) -> list[dict]:
	"""Distribute `total` across members.

	members: [{"employee":..., "hours":.., "skill_weight":..}]
	Returns the same dicts enriched with share_percent and amount. The rounding
	residual is pushed to the largest share so amounts always sum to total exactly.
	"""
	if not members:
		return []
	weights = []
	for m in members:
		hours = f(m.get("hours"))
		skill = f(m.get("skill_weight")) or 1.0
		if method == SPLIT_EQUAL:
			w = 1.0
		elif method == SPLIT_SKILL:
			w = skill
		elif method == SPLIT_HOURS_SKILL:
			w = hours * skill
		else:
			w = hours
		weights.append(max(w, 0.0))
	total_w = sum(weights)
	if total_w <= 0:  # nobody has hours -> fall back to equal split
		weights = [1.0] * len(members)
		total_w = float(len(members))

	out, allocated = [], 0.0
	for m, w in zip(members, weights):
		share = w / total_w
		amount = r(f(total) * share)
		allocated += amount
		out.append({**m, "share_percent": r(share * 100, 4), "amount": amount})
	residual = r(f(total) - allocated)
	if residual and out:
		idx = max(range(len(out)), key=lambda i: out[i]["share_percent"])
		out[idx]["amount"] = r(out[idx]["amount"] + residual)
	return out


# ---------------------------------------------------------------- slabs
def slab_incentive(total_pieces: float, base_rate: float, slabs: list[dict]) -> float:
	"""Marginal (tax-bracket style) incentive on top of base pay.

	slabs: [{"from_qty": 0, "to_qty": 500, "rate_multiplier": 1.0},
	        {"from_qty": 500, "to_qty": 0, "rate_multiplier": 1.2}]   (to_qty 0 = open ended)
	Returns only the *extra* over base: pieces_in_slab x rate x (multiplier - 1).
	"""
	total_pieces, base_rate = f(total_pieces), f(base_rate)
	extra = 0.0
	for s in sorted(slabs or [], key=lambda x: f(x.get("from_qty"))):
		lo = f(s.get("from_qty"))
		hi = f(s.get("to_qty")) or float("inf")
		if total_pieces <= lo:
			continue
		in_slab = min(total_pieces, hi) - lo
		if in_slab > 0:
			extra += in_slab * base_rate * ((f(s.get("rate_multiplier")) or 1.0) - 1.0)
	return r(extra)


# ---------------------------------------------------------------- quality
@dataclass
class QAImpact:
	original_deduction: float
	rework_payout: float
	material_recovery: float
	total_worker_deduction: float


def qa_financials(
	disposition: str,
	qty_failed: float,
	unit_rate: float,
	worker_responsible: bool = True,
	original_payout_percent: float = 0,
	third_party_deduction_percent: float = 100,
	third_party_payout_percent: float = 100,
	material_unit_cost: float = 0,
	material_recovery_percent: float = 0,
) -> QAImpact:
	"""Multi-path defect routing.

	Scrap                    : original keeps `original_payout_percent` of failed-piece pay;
	                           optional material recovery charge.
	Rework - Original Worker : failed-piece pay removed, original earns `original_payout_percent`
	                           back for doing the rework.
	Rework - Third Party     : original loses `third_party_deduction_percent`, specialist earns
	                           `third_party_payout_percent`.
	If the defect is not the worker's fault (machine / material / design) the worker is
	never charged; a third-party rework payout is still earned by the specialist.
	"""
	failed, rate = f(qty_failed), f(unit_rate)
	base = failed * rate
	deduction = rework = recovery = 0.0

	if disposition == DISP_SCRAP:
		deduction = base * (1 - f(original_payout_percent) / 100.0)
		recovery = failed * f(material_unit_cost) * f(material_recovery_percent) / 100.0
	elif disposition == DISP_REWORK_ORIGINAL:
		deduction = base
		rework = base * f(original_payout_percent) / 100.0
	elif disposition == DISP_REWORK_THIRD:
		deduction = base * f(third_party_deduction_percent) / 100.0
		rework = base * f(third_party_payout_percent) / 100.0

	if not worker_responsible:
		deduction = 0.0
		recovery = 0.0
		if disposition == DISP_REWORK_ORIGINAL:
			rework = 0.0  # worker was never charged, so no rework "give-back"

	deduction, rework, recovery = r(max(deduction, 0)), r(max(rework, 0)), r(max(recovery, 0))
	return QAImpact(deduction, rework, recovery, r(deduction + recovery))


# ---------------------------------------------------------------- labour-law guards
def deduction_cap_adjustment(
	earnings: float,
	damage_deductions: float,
	days_worked: int,
	cap_days: float = 5,
	total_cap_percent: float = 50,
	fixed_daily_wage: float = 0,
) -> float:
	"""Amount to waive (add back) so deductions respect statutory ceilings.

	Defaults mirror the commonly cited KSA Labor Law limits (damage deductions capped
	at 5 days' wage per month; total deductions capped at half the wage). Both caps
	are configurable in PieceWork Settings - confirm with counsel for your jurisdiction.
	"""
	earnings, damage = f(earnings), f(damage_deductions)
	if damage <= 0:
		return 0.0
	daily = f(fixed_daily_wage) or (earnings / days_worked if days_worked else 0.0)
	caps = []
	if f(cap_days) > 0:
		caps.append(daily * f(cap_days))
	if f(total_cap_percent) > 0:
		caps.append(earnings * f(total_cap_percent) / 100.0)
	if not caps:
		return 0.0
	allowed = max(min(caps), 0.0)
	return r(max(damage - allowed, 0.0))


def minimum_topup(earnings: float, days_worked: int, minimum_daily_earning: float) -> float:
	floor = f(days_worked) * f(minimum_daily_earning)
	return r(max(floor - f(earnings), 0.0))


def efficiency_percent(earned_minutes: float, hours_worked: float) -> float:
	if f(hours_worked) <= 0:
		return 0.0
	return r(f(earned_minutes) / (f(hours_worked) * 60.0) * 100.0, 1)


def ole(availability_pct: float, performance_pct: float, quality_pct: float) -> float:
	"""Overall Labor Effectiveness = A x P x Q (all as percentages)."""
	return r(f(availability_pct) * f(performance_pct) * f(quality_pct) / 10000.0, 1)
