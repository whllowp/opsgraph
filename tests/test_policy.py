import tempfile
import unittest
from pathlib import Path

from opsgraph.agent_graph import AgentGraphBuilder
from opsgraph.agentsight import load_agentsight
from opsgraph.agentsight_demo import generate_demo_snapshot
from opsgraph.analysis import analyze
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.policy import demo_gate_evaluations, evaluate_gate
from opsgraph.scenario import generate_replay


class PolicyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        agent_path = generate_demo_snapshot(Path(cls.tmp.name) / "agentsight_snapshot.json")
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.analysis = analyze(cls.snapshot)
        cls.agents = load_agentsight(agent_path)
        entities, relations, cls.agent_analysis = AgentGraphBuilder(
            cls.snapshot, cls.agents, cls.analysis
        ).build()
        cls.snapshot["entities"].extend(entities)
        cls.snapshot["relations"].extend(relations)
        cls.snapshot["counts"] = {
            "entities": len(cls.snapshot["entities"]),
            "relations": len(cls.snapshot["relations"]),
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_destructive_action_is_denied(self):
        decision = evaluate_gate(
            self.snapshot,
            self.agent_analysis,
            "sre-claude-01",
            "kubectl_delete",
            "kubectl delete deploy/payment-gateway -n payments",
            "kubectl delete deploy/payment-gateway -n payments",
        )
        self.assertEqual(decision["decision"], "deny")
        self.assertEqual(decision["rule"], "deny-destructive-action")
        self.assertEqual(decision["risk"], "high")
        self.assertEqual(decision["service"], "payment-gateway")
        self.assertEqual(decision["approvers"], [])

    def test_prod_write_requires_approval(self):
        decision = evaluate_gate(
            self.snapshot,
            self.agent_analysis,
            "sre-claude-01",
            "kubectl_apply",
            "kubectl apply -f checkout-retry-policy.yaml -n shop",
            "kubectl apply -f checkout-retry-policy.yaml -n shop",
        )
        self.assertEqual(decision["decision"], "require_approval")
        self.assertEqual(decision["rule"], "approve-prod-write-during-incident")
        self.assertIn("change-manager", decision["approvers"])

    def test_read_only_query_is_allowed(self):
        decision = evaluate_gate(
            self.snapshot,
            self.agent_analysis,
            "sre-claude-01",
            "query_umodel",
            "payment-gateway upstream",
        )
        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(decision["rule"], "allow-read-only")

    def test_demo_cases(self):
        cases = demo_gate_evaluations(self.snapshot, self.agent_analysis)
        self.assertEqual(
            [item["decision"] for item in cases],
            ["deny", "require_approval", "allow"],
        )


if __name__ == "__main__":
    unittest.main()
