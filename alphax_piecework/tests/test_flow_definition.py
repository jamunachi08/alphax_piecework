"""Offline checks on the flow definition. No site or database needed:

	python3 -m unittest alphax_piecework.tests.test_flow_definition
"""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_flow_constants():
	"""Read the definitions out of engine/flow.py without importing frappe."""
	tree = ast.parse(open(os.path.join(ROOT, "engine", "flow.py")).read())
	out = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
			name = node.targets[0].id
			if name in ("LANES", "NODES", "EDGES", "SEQUENCE", "KEY_OUTPUTS"):
				out[name] = ast.literal_eval(node.value)
	return out


F = load_flow_constants()
LANES, NODES, EDGES = F["LANES"], F["NODES"], F["EDGES"]
SEQUENCE, KEY_OUTPUTS = F["SEQUENCE"], F["KEY_OUTPUTS"]
NODE_IDS = {n["id"] for n in NODES}
LANE_IDS = {lane["id"] for lane in LANES}


class TestFlowDefinition(unittest.TestCase):
	def test_five_lanes_match_the_chart(self):
		self.assertEqual([lane["id"] for lane in LANES],
						 ["setup", "shopfloor", "quality", "supervision", "payroll"])
		for lane in LANES:
			self.assertTrue(lane["ar"], f"{lane['id']} has no Arabic label")

	def test_node_ids_unique(self):
		self.assertEqual(len(NODE_IDS), len(NODES))

	def test_every_node_sits_in_a_real_lane(self):
		for node in NODES:
			self.assertIn(node["lane"], LANE_IDS, node["id"])

	def test_every_edge_connects_real_nodes(self):
		for edge in EDGES:
			self.assertIn(edge["from"], NODE_IDS, edge)
			self.assertIn(edge["to"], NODE_IDS, edge)

	def test_no_node_is_stranded(self):
		linked = {e["from"] for e in EDGES} | {e["to"] for e in EDGES}
		self.assertEqual(NODE_IDS - linked, set())

	def test_numbered_steps_run_in_order(self):
		steps = [n["step"] for n in NODES if n.get("step")]
		self.assertEqual(steps, list(range(1, len(steps) + 1)))

	def test_sequence_covers_every_required_step(self):
		required = [n["id"] for n in NODES if n.get("step") and not n.get("optional")]
		self.assertEqual(set(SEQUENCE), set(required))

	def test_optional_stages_are_still_drawn_and_linked(self):
		for node in NODES:
			if node.get("optional"):
				self.assertNotIn(node["id"], SEQUENCE, node["id"])
				self.assertTrue([e for e in EDGES if e["from"] == node["id"]], node["id"])

	def test_each_sequence_stage_has_a_doctype(self):
		by_id = {n["id"]: n for n in NODES}
		for stage in SEQUENCE:
			self.assertTrue(by_id[stage].get("doctype"), stage)

	def test_decisions_have_both_branches(self):
		for node in NODES:
			if node["kind"] != "decision":
				continue
			labels = {e.get("label") for e in EDGES if e["from"] == node["id"]}
			self.assertEqual(labels, {"Yes", "No"}, node["id"])

	def test_every_loop_returns_into_the_flow(self):
		for node in NODES:
			if node.get("loop"):
				targets = [e["to"] for e in EDGES if e["from"] == node["id"]]
				self.assertTrue(targets, node["id"])

	def test_terminators_start_and_end_the_chart(self):
		terminators = [n["id"] for n in NODES if n["kind"] == "terminator"]
		self.assertEqual(sorted(terminators), ["end", "start"])
		self.assertEqual([e["to"] for e in EDGES if e["from"] == "start"], ["rates"])
		self.assertEqual([e["from"] for e in EDGES if e["to"] == "end"], ["audit"])

	def test_key_outputs_cover_all_five_lanes(self):
		self.assertEqual({o["lane"] for o in KEY_OUTPUTS}, LANE_IDS)
		self.assertEqual([o["n"] for o in KEY_OUTPUTS], [1, 2, 3, 4, 5])
		for out in KEY_OUTPUTS:
			self.assertTrue(out["items"])

	def test_reports_referenced_by_nodes_exist_on_disk(self):
		report_dir = os.path.join(ROOT, "alphax_piecework", "report")
		available = {d for d in os.listdir(report_dir) if os.path.isdir(os.path.join(report_dir, d))}
		for node in NODES:
			if node.get("report"):
				folder = node["report"].lower().replace(" ", "_")
				self.assertIn(folder, available, node["report"])

	def test_date_fields_are_declared_for_non_posting_date_doctypes(self):
		for node in NODES:
			if node.get("doctype") in ("Shop Floor QA Log", "PieceWork Edge Event", "PieceWork Audit Event"):
				self.assertTrue(node.get("date_field"), node["id"])

	def test_doctypes_referenced_by_nodes_exist_on_disk(self):
		doctype_dir = os.path.join(ROOT, "alphax_piecework", "doctype")
		available = {d for d in os.listdir(doctype_dir) if os.path.isdir(os.path.join(doctype_dir, d))}
		for node in NODES:
			if node.get("doctype"):
				self.assertIn(node["doctype"].lower().replace(" ", "_"), available, node["doctype"])


if __name__ == "__main__":
	unittest.main()
