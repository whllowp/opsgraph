import csv
import tempfile
import unittest
from pathlib import Path

from opsgraph import scenario
from opsgraph.config import SCENARIO_START


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = scenario.generate_replay(Path(cls.tmp.name))
        with (Path(cls.tmp.name) / "l7_flow_log.csv").open(encoding="utf-8") as handle:
            cls.rows = list(csv.DictReader(handle))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_deterministic_shape(self):
        self.assertGreater(len(self.rows), 1200)
        self.assertEqual(self.result["node_rows"], 60 * 6)
        fields = set(self.rows[0].keys())
        for required in (
            "pod_service_0",
            "pod_service_1",
            "request_domain",
            "endpoint",
            "rrt_p99",
            "is_internet_1",
        ):
            self.assertIn(required, fields)

    def test_retry_curve_precedes_latency(self):
        # The amplification curve starts at 00:10, while payment latency only
        # feels it after the queueing lag.
        self.assertAlmostEqual(scenario.retry_amplification(9), 1.0)
        self.assertGreater(scenario.retry_amplification(10), 1.5)
        self.assertAlmostEqual(scenario.retry_amplification(40), 8.0)

    def test_payment_edge_grows_more_than_traffic(self):
        def rps_for(minute, src, dst):
            values = [
                int(row["request"]) / 60
                for row in self.rows
                if int(row["time"]) == SCENARIO_START + minute * 60
                and row["pod_service_0"] == src
                and row["pod_service_1"] == dst
            ]
            return sum(values)

        baseline = rps_for(0, "checkout-service", "payment-gateway")
        peak = rps_for(40, "checkout-service", "payment-gateway")
        traffic = rps_for(40, "frontend", "checkout-service") / max(
            1.0, rps_for(0, "frontend", "checkout-service")
        )
        self.assertGreater(peak / baseline, 7.0)
        self.assertGreater(peak / baseline, traffic * 2.0)

    def test_shadow_edge_starts_late_and_is_undeclared(self):
        rows = [
            row
            for row in self.rows
            if row["pod_service_1"] == "" and row["request_domain"] == "api.promo-flash.com"
        ]
        self.assertTrue(rows)
        self.assertTrue(all(int(row["time"]) >= SCENARIO_START + 31 * 60 for row in rows))
        declared = scenario.declared_topology()
        edge = next(item for item in declared["edges"] if item["dst"] == "promo-shadow-api")
        self.assertFalse(edge["declared"])


if __name__ == "__main__":
    unittest.main()
