import json
import tempfile
import unittest
from pathlib import Path

from opsgraph.model_pack import build_model_pack
from opsgraph.ontology import ENTITY_SETS, LINKS


class ModelPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.result = build_model_pack(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts(self):
        self.assertEqual(self.result["entity_sets"], 16)
        self.assertEqual(self.result["links"], 21)
        self.assertEqual(len(list((self.root / "entity_set").glob("*.json"))), 16)
        self.assertEqual(len(list((self.root / "link").glob("*.json"))), 21)

    def test_entity_set_shape(self):
        element = json.loads(
            (self.root / "entity_set" / "svc.service.json").read_text(encoding="utf-8")
        )
        self.assertEqual(element["kind"], "entity_set")
        self.assertEqual(element["metadata"]["name"], "svc.service")
        self.assertEqual(element["metadata"]["domain"], "svc")
        self.assertIn("fields", element["spec"])
        self.assertEqual(element["spec"]["primary_key_fields"], ["id"])

    def test_link_shape(self):
        element = json.loads(
            (self.root / "link" / "svc.service_calls_svc.service.json").read_text(encoding="utf-8")
        )
        self.assertEqual(element["kind"], "entity_set_link")
        self.assertEqual(element["spec"]["entity_link_type"], "calls")
        self.assertEqual(element["spec"]["src"]["name"], "svc.service")
        self.assertEqual(element["spec"]["dest"]["name"], "svc.service")

    def test_names_unique(self):
        names = list(ENTITY_SETS.keys()) + [item["name"] for item in LINKS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
