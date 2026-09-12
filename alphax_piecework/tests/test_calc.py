"""Pure-math tests. Run with:  python -m unittest alphax_piecework.tests.test_calc
or inside a bench:              bench --site <site> run-tests --module alphax_piecework.tests.test_calc
"""

import unittest
from datetime import datetime

from alphax_piecework.engine import calc


class TestCapacity(unittest.TestCase):
	def test_rated_speed(self):
		limit, basis = calc.capacity_limit(8, rated_pieces_per_hour=100, basis=calc.CAPACITY_RATED)
		self.assertEqual(limit, 800)
		self.assertEqual(basis, calc.CAPACITY_RATED)

	def test_sam_with_efficiency(self):
		# 8h x 60 / 2 SAM = 240 pieces at 100 %; 130 % ceiling -> 312
		limit, basis = calc.capacity_limit(8, sam_minutes=2, max_efficiency_percent=130, basis=calc.CAPACITY_SAM)
		self.assertEqual(limit, 312)

	def test_stricter_picks_min_and_skill_factor(self):
		limit, basis = calc.capacity_limit(
			8, rated_pieces_per_hour=100, sam_minutes=2, max_efficiency_percent=130, skill_factor_percent=50
		)
		self.assertEqual(basis, calc.CAPACITY_SAM)
		self.assertEqual(limit, 156)

	def test_no_basis(self):
		self.assertEqual(calc.capacity_limit(8), (None, None))

	def test_hours_between(self):
		self.assertEqual(calc.hours_between(datetime(2026, 1, 1, 8), datetime(2026, 1, 1, 12, 30)), 4.5)


class TestEarnings(unittest.TestCase):
	def test_overtime_and_premium(self):
		e = calc.compute_earnings(
			qty=100, rate=1.0, hours=10, overtime_hours=2, overtime_multiplier=1.5,
			urgent_premium_percent=10, multi_skill_bonus_percent=5,
		)
		self.assertEqual(e.regular_amount, 80.0)
		self.assertEqual(e.overtime_amount, 30.0)
		self.assertEqual(e.premium_amount, 16.5)
		self.assertEqual(e.gross_amount, 126.5)

	def test_half_up_rounding(self):
		self.assertEqual(calc.r(2.675), 2.68)

	def test_specificity(self):
		self.assertGreater(calc.specificity_score(item_code="X"), calc.specificity_score(workstation_type="W", difficulty_class="D", item_group="G"))


class TestSplit(unittest.TestCase):
	def test_split_sums_exactly(self):
		members = [{"employee": "A", "hours": 1}, {"employee": "B", "hours": 1}, {"employee": "C", "hours": 1}]
		out = calc.split_amount(100, members, calc.SPLIT_HOURS)
		self.assertAlmostEqual(sum(m["amount"] for m in out), 100.0, places=2)

	def test_hours_x_skill(self):
		members = [{"employee": "A", "hours": 8, "skill_weight": 1.5}, {"employee": "B", "hours": 4, "skill_weight": 1}]
		out = calc.split_amount(160, members, calc.SPLIT_HOURS_SKILL)
		self.assertEqual(out[0]["amount"], 120.0)
		self.assertEqual(out[1]["amount"], 40.0)

	def test_zero_hours_falls_back_to_equal(self):
		out = calc.split_amount(10, [{"employee": "A"}, {"employee": "B"}], calc.SPLIT_HOURS)
		self.assertEqual([m["amount"] for m in out], [5.0, 5.0])


class TestSlabs(unittest.TestCase):
	def test_marginal(self):
		slabs = [
			{"from_qty": 0, "to_qty": 500, "rate_multiplier": 1.0},
			{"from_qty": 500, "to_qty": 800, "rate_multiplier": 1.2},
			{"from_qty": 800, "to_qty": 0, "rate_multiplier": 1.5},
		]
		# 300 x 0.2 + 100 x 0.5 = 110 extra at rate 1
		self.assertEqual(calc.slab_incentive(900, 1.0, slabs), 110.0)
		self.assertEqual(calc.slab_incentive(400, 1.0, slabs), 0.0)


class TestQA(unittest.TestCase):
	def test_scrap_with_recovery(self):
		i = calc.qa_financials(calc.DISP_SCRAP, 10, 2.0, material_unit_cost=5, material_recovery_percent=10)
		self.assertEqual((i.original_deduction, i.material_recovery, i.total_worker_deduction), (20.0, 5.0, 25.0))

	def test_third_party(self):
		i = calc.qa_financials(calc.DISP_REWORK_THIRD, 10, 2.0, third_party_deduction_percent=60, third_party_payout_percent=50)
		self.assertEqual((i.original_deduction, i.rework_payout), (12.0, 10.0))

	def test_not_worker_fault(self):
		i = calc.qa_financials(calc.DISP_REWORK_THIRD, 10, 2.0, worker_responsible=False)
		self.assertEqual(i.original_deduction, 0.0)
		self.assertEqual(i.rework_payout, 20.0)


class TestLabourGuards(unittest.TestCase):
	def test_cap(self):
		# earnings 3000 over 30 days -> daily 100 -> 5-day cap 500; 50 % cap 1500 -> min 500
		self.assertEqual(calc.deduction_cap_adjustment(3000, 800, 30, 5, 50), 300.0)
		self.assertEqual(calc.deduction_cap_adjustment(3000, 400, 30, 5, 50), 0.0)

	def test_topup(self):
		self.assertEqual(calc.minimum_topup(500, 10, 60), 100.0)

	def test_ole(self):
		self.assertEqual(calc.ole(90, 95, 99), 84.6)


if __name__ == "__main__":
	unittest.main()
