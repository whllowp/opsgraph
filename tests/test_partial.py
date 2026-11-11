import unittest

from opsgraph.partial import coverage, run_partial_rea


class PartialRCATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_partial_rea(10)

    def test_coverage_is_sixty_percent(self):
        info = coverage()
        self.assertEqual(info["service_pct"], 60.0)
        self.assertEqual(info["edge_pct"], 60.0)
        self.assertIn("ledger-service", info["blind_services"])
        self.assertIn("risk-control", info["blind_services"])

    def test_ten_iterations_infer_blind_root_and_recover(self):
        summary = self.result["summary"]
        self.assertEqual(summary["iterations"], 10)
        self.assertEqual(summary["root_service"], "ledger-service")
        self.assertEqual(summary["root_cause"], "DB 连接池耗尽")
        self.assertGreater(summary["confidence"], 0.95)
        self.assertTrue(summary["recovered"])
        self.assertTrue(summary["blind_spot"])
        self.assertEqual(summary["final_p99_ms"], 620)

    def test_probe_chain_uses_multiple_evidence_sources(self):
        sources = {item.get("source") for item in self.result["iterations"]}
        self.assertTrue(
            {"coverage", "ebpf", "metrics", "logs", "config", "knowledge", "synthetic"} <= sources
        )
        knowledge = next(
            item for item in self.result["iterations"] if item.get("probe_id") == "knowledge-match"
        )
        self.assertEqual(knowledge["knowledge_match"], "db-pool-exhaustion")

    def test_remediation_and_verification(self):
        remediation = next(
            item for item in self.result["iterations"] if item["phase"] == "remediation"
        )
        verify = next(item for item in self.result["iterations"] if item["phase"] == "verify")
        coverage_step = next(
            item for item in self.result["iterations"] if item["phase"] == "coverage"
        )
        self.assertTrue(remediation["recovered"])
        self.assertEqual(remediation["before"]["payment_gateway_p99_ms"], 1620)
        self.assertEqual(remediation["after"]["payment_gateway_p99_ms"], 620)
        self.assertTrue(all(check["passed"] for check in verify["checks"]))
        self.assertEqual(coverage_step["coverage_after"]["service_pct"], 70.0)


if __name__ == "__main__":
    unittest.main()
