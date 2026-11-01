import tempfile
import unittest
from pathlib import Path

from opsgraph.analysis import analyze
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.result = analyze(cls.snapshot)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_root_cause_and_confidence(self):
        self.assertEqual(self.result["root_cause"], "checkout-service")
        self.assertEqual(self.result["victim"], "payment-gateway")
        self.assertGreater(self.result["confidence"], 0.7)
        self.assertEqual(self.result["ranking"][0]["service"], "checkout-service")
        self.assertEqual(self.result["ranking"][1]["service"], "payment-gateway")

    def test_change_dominates_the_score(self):
        top = self.result["ranking"][0]
        self.assertGreater(top["components"]["change_affinity"], 0.4)
        self.assertGreater(top["components"]["graph_position"], 0.1)

    def test_causal_path_and_findings(self):
        self.assertEqual(self.result["path"][0], "checkout-service")
        self.assertIn("payment-gateway", self.result["path"])
        self.assertIn("Alipay Gateway", self.result["path"])
        ids = {item["id"] for item in self.result["findings"]}
        self.assertTrue({"retry-storm", "shadow-dependency", "declared-unobserved"}.issubset(ids))

    def test_roles_and_journeys(self):
        services = {
            item["display_name"]: item
            for item in self.snapshot["entities"]
            if item["__entity_type__"] == "svc.service"
        }
        self.assertEqual(services["checkout-service"]["role"], "root_cause")
        self.assertEqual(services["payment-gateway"]["role"], "victim")
        journeys = [
            item for item in self.snapshot["entities"] if item["__entity_type__"] == "biz.journey"
        ]
        self.assertTrue(any(item["impacted"] for item in journeys))
        self.assertEqual(len(self.result["impact"]["journeys"]), 3)

    def test_timeline_is_sorted(self):
        timestamps = [item["ts"] for item in self.result["timeline"]]
        self.assertEqual(timestamps, sorted(timestamps))


if __name__ == "__main__":
    unittest.main()
