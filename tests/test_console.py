import tempfile
import unittest
from pathlib import Path

from opsgraph.agent_graph import AgentGraphBuilder
from opsgraph.agentsight import load_agentsight
from opsgraph.agentsight_demo import generate_demo_snapshot
from opsgraph.analysis import analyze
from opsgraph.console_server import build_console_state
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class ConsoleStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        agent_path = generate_demo_snapshot(Path(cls.tmp.name) / "agentsight_snapshot.json")
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.analysis = analyze(cls.snapshot)
        agents = load_agentsight(agent_path)
        entities, relations, cls.agent_analysis = AgentGraphBuilder(
            cls.snapshot, agents, cls.analysis
        ).build()
        cls.snapshot["entities"].extend(entities)
        cls.snapshot["relations"].extend(relations)
        cls.snapshot["counts"] = {
            "entities": len(cls.snapshot["entities"]),
            "relations": len(cls.snapshot["relations"]),
        }
        cls.state = build_console_state(
            cls.snapshot,
            cls.analysis,
            {"addr": "http://127.0.0.1:18080", "connected": True, "provider": "memory"},
            cls.agent_analysis,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_nodes_and_layout(self):
        names = {node["name"] for node in self.state["nodes"]}
        self.assertIn("checkout-service", names)
        self.assertIn("Alipay Gateway", names)
        self.assertIn("Standard Checkout", names)
        for node in self.state["nodes"]:
            self.assertGreater(node["x"], 0)
            self.assertGreater(node["y"], 0)

    def test_agent_lane(self):
        types = {node["type"] for node in self.state["nodes"]}
        self.assertIn("ai.agent_session", types)
        edge_types = {edge["type"] for edge in self.state["edges"]}
        self.assertIn("acts_on", edge_types)
        self.assertIn("delegates_to", edge_types)
        self.assertEqual(self.state["agents"]["summary"]["sessions"], 4)

    def test_edges_and_path(self):
        types = {edge["type"] for edge in self.state["edges"]}
        self.assertIn("calls", types)
        self.assertIn("depends_on", types)
        self.assertEqual(self.state["analysis"]["root_cause"], "checkout-service")

    def test_metadata(self):
        self.assertEqual(self.state["meta"]["model"]["entity_sets"], 16)
        self.assertEqual(self.state["meta"]["graph_counts"]["entities"], 130)
        self.assertEqual(self.state["queries"][0]["label"], "在 UModel 中定位受害服务")


if __name__ == "__main__":
    unittest.main()
