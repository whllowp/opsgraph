import tempfile
import unittest
from pathlib import Path

from opsgraph.agent_graph import AgentGraphBuilder
from opsgraph.agentsight import load_agentsight, load_snapshot
from opsgraph.agentsight_demo import generate_demo_snapshot
from opsgraph.analysis import analyze
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class AgentSightLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = generate_demo_snapshot(Path(cls.tmp.name) / "agentsight_snapshot.json")
        cls.agents = load_snapshot(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot_shape(self):
        self.assertEqual(self.agents.schema_version, 1)
        self.assertEqual(len(self.agents.sessions), 2)
        self.assertEqual(len(self.agents.tool_calls), 16)
        self.assertGreaterEqual(len(self.agents.effects), 10)
        self.assertEqual(len(self.agents.models), 2)
        self.assertEqual(self.agents.sessions[0].key, "sre-claude-01")

    def test_alias_and_pid_resolution(self):
        aliases = self.agents.session_aliases()
        self.assertIn("sre-claude-01", aliases)
        self.assertIn("pid:6101", aliases)
        self.assertEqual(aliases["sre-claude-01"].model, "claude-sonnet-4")


class AgentGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        agent_path = generate_demo_snapshot(Path(cls.tmp.name) / "agentsight_snapshot.json")
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.service_analysis = analyze(cls.snapshot)
        cls.agents = load_agentsight(agent_path)
        cls.entities, cls.relations, cls.analysis = AgentGraphBuilder(
            cls.snapshot, cls.agents, cls.service_analysis
        ).build()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_graph_counts(self):
        types = {}
        for item in self.entities:
            types[item["__entity_type__"]] = types.get(item["__entity_type__"], 0) + 1
        self.assertEqual(types["ai.agent_session"], 2)
        self.assertEqual(types["ai.model"], 2)
        self.assertEqual(types["ai.tool_call"], 16)
        self.assertGreaterEqual(types["ai.effect"], 10)
        self.assertGreater(len(self.relations), 25)

    def test_agent_session_links_to_service(self):
        links = [item for item in self.relations if item["__relation_type__"] == "acts_on"]
        self.assertEqual(len(links), 1)
        session = next(
            item for item in self.entities if item["display_name"] == "claude:sre-claude-01"
        )
        self.assertEqual(links[0]["__src_entity_id__"], session["__entity_id__"])
        self.assertEqual(session["claim_status"], "verified")
        self.assertEqual(session["remediation_status"], "pending_validation")
        self.assertEqual(session["risk_level"], "high")

    def test_retry_loops_and_risky_effects(self):
        self.assertGreaterEqual(len(self.analysis["loops"]), 2)
        self.assertGreaterEqual(len(self.analysis["risk_effects"]), 3)
        summary = self.analysis["summary"]
        self.assertEqual(summary["sessions"], 2)
        self.assertEqual(summary["claims_verified"], 1)
        self.assertGreater(summary["agent_entities"], 20)

    def test_agent_findings(self):
        ids = {item["id"] for item in self.analysis["findings"]}
        self.assertIn("agent-risky-effects", ids)
        self.assertIn("agent-claim-verified", ids)
        self.assertIn("agent-remediation-pending", ids)


if __name__ == "__main__":
    unittest.main()
