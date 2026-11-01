import unittest

from opsgraph.analysis import analyze
from opsgraph.console_server import build_console_state
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer


class ConsoleStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flows, nodes, declared = load_dataset("replay")
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.analysis = analyze(cls.snapshot)
        cls.state = build_console_state(
            cls.snapshot,
            cls.analysis,
            {"addr": "http://127.0.0.1:18080", "connected": True, "provider": "memory"},
        )

    def test_nodes_and_layout(self):
        names = {node["name"] for node in self.state["nodes"]}
        self.assertIn("checkout-service", names)
        self.assertIn("Alipay Gateway", names)
        self.assertIn("Standard Checkout", names)
        for node in self.state["nodes"]:
            self.assertGreater(node["x"], 0)
            self.assertGreater(node["y"], 0)

    def test_edges_and_path(self):
        types = {edge["type"] for edge in self.state["edges"]}
        self.assertIn("calls", types)
        self.assertIn("depends_on", types)
        self.assertEqual(self.state["analysis"]["root_cause"], "checkout-service")

    def test_metadata(self):
        self.assertEqual(self.state["meta"]["model"]["entity_sets"], 12)
        self.assertEqual(self.state["meta"]["graph_counts"]["entities"], 86)
        self.assertEqual(self.state["queries"][0]["label"], "在 UModel 中定位受害服务")


if __name__ == "__main__":
    unittest.main()
