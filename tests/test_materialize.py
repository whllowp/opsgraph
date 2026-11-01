import tempfile
import unittest
from pathlib import Path

from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class MaterializerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.entities = cls.snapshot["entities"]
        cls.relations = cls.snapshot["relations"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_and_ids(self):
        self.assertEqual(len(self.entities), 86)
        self.assertEqual(len(self.relations), 151)
        for entity in self.entities:
            self.assertEqual(len(entity["__entity_id__"]), 32)
            self.assertEqual(entity["__entity_id__"], entity["id"])

    def test_service_metrics(self):
        services = {
            item["display_name"]: item
            for item in self.entities
            if item["__entity_type__"] == "svc.service"
        }
        self.assertGreater(services["payment-gateway"]["latency_p99_ms"], 2000)
        self.assertGreater(services["payment-gateway"]["p99_delta_pct"], 1000)
        self.assertGreater(services["checkout-service"]["outbound_amp"], 4)
        self.assertEqual(services["batch-reporter"]["status"], "healthy")

    def test_shadow_and_dead_edges(self):
        shadow = [item for item in self.relations if item.get("shadow")]
        self.assertEqual(len(shadow), 1)
        self.assertFalse(shadow[0]["declared"])
        self.assertGreater(shadow[0]["rps"], 100)
        dead = [item for item in self.relations if item.get("observed") is False]
        self.assertEqual(len(dead), 1)
        self.assertTrue(dead[0]["declared"])
        self.assertEqual(dead[0]["rps"], 0)

    def test_evidence_is_attached_to_edges(self):
        edge = next(
            item
            for item in self.relations
            if item.get("__relation_type__") == "calls"
            and item.get("display_name") == "checkout-service calls payment-gateway"
        )
        self.assertEqual(edge["evidence"], "deepflow.l7_flow_log")
        self.assertGreater(edge["requests_per_min"], 0)
        self.assertIsNotNone(edge["onset_ts"])


if __name__ == "__main__":
    unittest.main()
