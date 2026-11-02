import tempfile
import unittest
from pathlib import Path

from opsgraph.code_cases import FINTECH_ROOT
from opsgraph.code_graph import build_code_graph
from opsgraph.code_rca import run_rea_loop
from opsgraph.deepflow import load_dataset
from opsgraph.materialize import Materializer
from opsgraph.scenario import generate_replay


class CodeGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        generate_replay(Path(cls.tmp.name))
        flows, nodes, declared = load_dataset("replay", replay_dir=cls.tmp.name)
        cls.snapshot = Materializer(flows, nodes, declared).build()
        cls.result = run_rea_loop(max_iterations=10)
        cls.entities, cls.relations, cls.summary = build_code_graph(
            cls.result, FINTECH_ROOT, cls.snapshot
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts(self):
        self.assertEqual(self.summary["issues"], 10)
        self.assertEqual(self.summary["patches"], 10)
        self.assertEqual(self.summary["files"], 6)
        self.assertEqual(self.summary["entities"], 47)
        self.assertGreaterEqual(self.summary["relations"], 70)

    def test_code_domain_shape(self):
        types = {}
        for item in self.entities:
            types[item["__entity_type__"]] = types.get(item["__entity_type__"], 0) + 1
        self.assertEqual(types["code.issue"], 10)
        self.assertEqual(types["code.patch"], 10)
        self.assertEqual(types["code.line"], 10)
        self.assertEqual(types["code.repository"], 1)

    def test_issue_links_to_service_and_patch(self):
        types = [item["__relation_type__"] for item in self.relations]
        self.assertIn("fixes", types)
        self.assertIn("modifies", types)
        self.assertIn("observed_in", types)
        service_links = [
            item for item in self.relations if item["__dest_entity_type__"] == "svc.service"
        ]
        self.assertEqual(len(service_links), 10)


if __name__ == "__main__":
    unittest.main()
