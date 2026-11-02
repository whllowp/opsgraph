import tempfile
import unittest
from pathlib import Path

from opsgraph.agent_graph import AgentGraphBuilder
from opsgraph.agentsight import load_agentsight
from opsgraph.agentsight_demo import generate_demo_snapshot
from opsgraph.analysis import analyze
from opsgraph.deepflow import load_dataset
from opsgraph.history import compute_baseline, load_history
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class AgentBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        agent_path = generate_demo_snapshot(Path(cls.tmp.name) / "agentsight_snapshot.json")
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        snapshot = Materializer(flows, nodes, declared).build()
        analysis = analyze(snapshot)
        agents = load_agentsight(agent_path)
        _, _, cls.agent_analysis = AgentGraphBuilder(snapshot, agents, analysis).build()
        cls.history = load_history()
        cls.baseline = compute_baseline(cls.history, cls.agent_analysis)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_history_and_accuracy(self):
        self.assertEqual(self.baseline["history_runs"], 8)
        overall = self.baseline["claim_accuracy"]["overall"]
        self.assertEqual(overall["correct"], 6)
        self.assertEqual(overall["total"], 8)
        self.assertAlmostEqual(overall["accuracy"], 0.75)

    def test_root_session_baseline(self):
        root = next(
            item for item in self.baseline["sessions"] if item["name"] == "claude:sre-claude-01"
        )
        self.assertEqual(root["historical_runs"], 5)
        self.assertEqual(root["metrics"]["total_tokens"]["current"], 44600)
        self.assertGreater(root["metrics"]["total_tokens"]["p95"], 0)

    def test_baseline_spikes(self):
        ids = {item["id"] for item in self.baseline["findings"]}
        self.assertIn("agent-baseline-spike", ids)
        details = " ".join(item["detail"] for item in self.baseline["findings"])
        self.assertIn("total_tokens", details)
        self.assertIn("retry_loops", details)


if __name__ == "__main__":
    unittest.main()
