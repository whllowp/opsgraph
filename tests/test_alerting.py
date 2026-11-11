import tempfile
import unittest
from pathlib import Path

from opsgraph.alerting import run_alerting, write_alert_artifacts


def snapshot() -> dict:
    return {
        "window": {
            "start": 1793548800,
            "end": 1793552400,
            "start_iso": "2026-11-02T00:00:00+08:00",
            "end_iso": "2026-11-02T01:00:00+08:00",
        },
        "entities": [
            {
                "__entity_type__": "svc.service",
                "display_name": "checkout-service",
                "role": "root_cause",
                "latency_p99_ms": 1200,
                "baseline_p99_ms": 100,
                "error_rate": 0.02,
            },
            {
                "__entity_type__": "svc.service",
                "display_name": "payment-gateway",
                "role": "victim",
                "latency_p99_ms": 900,
                "baseline_p99_ms": 100,
                "error_rate": 0.02,
                "slo_p99_ms": 800,
            },
            {
                "__entity_type__": "svc.service",
                "display_name": "payment-router",
                "role": "downstream",
                "latency_p99_ms": 800,
                "baseline_p99_ms": 100,
                "error_rate": 0.02,
            },
            {
                "__entity_type__": "infra.node",
                "display_name": "node-3",
                "cpu_utilization": 0.92,
            },
            {
                "__entity_type__": "infra.pod",
                "display_name": "payment-gateway-0",
                "node": "node-4",
                "workload": "payment-gateway-deploy",
            },
            {
                "__entity_type__": "infra.pod",
                "display_name": "payment-router-0",
                "node": "node-4",
                "workload": "payment-router-deploy",
            },
            {
                "__entity_type__": "ops.alert",
                "display_name": "payment-gateway SLO burn",
                "target": "payment-gateway",
                "target_type": "svc.service",
                "severity": "P1",
                "status": "firing",
                "signal": "slo_burn_rate",
                "value": "2.8x",
                "fired_ts": 1793551200,
            },
        ],
    }


def analysis() -> dict:
    return {
        "root_cause": "checkout-service",
        "victim": "payment-gateway",
        "confidence": 0.8,
        "ranking": [
            {
                "service": "checkout-service",
                "confidence": 0.8,
                "reasons": ["risky retry config", "outbound amplification"],
            }
        ],
    }


class AlertingTests(unittest.TestCase):
    def test_policies_compress_storm_and_auto_rca(self):
        result = run_alerting(snapshot(), analysis())
        stats = result["stats"]
        self.assertGreater(stats["input_events"], 20)
        self.assertGreater(stats["deduped"], 0)
        self.assertEqual(stats["groups"], 1)
        self.assertEqual(stats["silenced"], 12)
        self.assertAlmostEqual(stats["compression_ratio"], 0.99, places=2)
        group = result["groups"][0]
        self.assertEqual(group["severity"], "P1")
        self.assertEqual(group["rca"]["root_cause"], "checkout-service")
        self.assertTrue(group["rca"]["auto"])
        self.assertGreater(group["inhibited_count"], 0)
        self.assertEqual(group["decision"]["source"], "laya")
        self.assertEqual(group["decision"]["action"], "page")
        self.assertGreater(group["decision"]["root_probability"], 0.8)
        self.assertFalse(result["brain"]["llm"]["enabled"])

    def test_node_failure_storm_groups_under_node(self):
        result = run_alerting(snapshot(), analysis(), node_failure=True)
        self.assertTrue(result["storm"]["detected"])
        self.assertGreater(result["storm"]["peak_per_minute"], 20)
        node_groups = [item for item in result["groups"] if item.get("root_node")]
        self.assertTrue(node_groups)
        node_group = node_groups[0]
        self.assertEqual(node_group["severity"], "P1")
        self.assertEqual(node_group["rca"]["root_cause"], "node-4")
        self.assertEqual(node_group["decision"]["action"], "page")
        self.assertIn("payment-gateway", node_group["impacted_services"])

    def test_artifact_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_alert_artifacts(run_alerting(snapshot(), analysis()), Path(tmp))
            self.assertTrue(Path(paths["json"]).exists())
            self.assertTrue(Path(paths["report"]).exists())


if __name__ == "__main__":
    unittest.main()
