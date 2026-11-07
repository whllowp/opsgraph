import unittest
from pathlib import Path

from opsgraph.brief import build_brief, render_brief_markdown
from opsgraph.codeowners import OwnersIndex


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "window": {"start_iso": "2026-11-02T00:00:00+08:00"},
            "entities": [
                {
                    "__entity_type__": "ops.incident",
                    "display_name": "Payment P99 SLO breach",
                    "severity": "P1",
                    "status": "investigating",
                    "victim": "payment-gateway",
                },
                {
                    "__entity_type__": "svc.service",
                    "display_name": "payment-gateway",
                    "owner": "payments-backend",
                    "latency_p99_ms": 2255,
                    "baseline_p99_ms": 110,
                    "p99_delta_pct": 1915,
                    "error_rate": 0.012,
                    "qps": 3334,
                    "slo_p99_ms": 800,
                },
                {
                    "__entity_type__": "svc.service",
                    "display_name": "checkout-service",
                    "owner": "commerce-core",
                },
            ],
        }
        self.analysis = {
            "root_cause": "checkout-service",
            "victim": "payment-gateway",
            "confidence": 0.779,
            "ranking": [
                {
                    "service": "checkout-service",
                    "reasons": ["risky config at 00:10", "outbound 4.6x"],
                }
            ],
            "summary": "checkout retry amplification",
        }
        self.agents = {
            "source": "agentsight.snapshot",
            "summary": {"high_risk_effects": 4, "claims_verified": 1, "claims_mismatched": 0},
            "sessions": [
                {
                    "name": "claude:sre-claude-01",
                    "risk_level": "high",
                    "remediation_status": "pending_validation",
                    "linked_service": "checkout-service",
                }
            ],
        }
        self.policy = [
            {
                "decision": "deny",
                "target": "kubectl delete deploy/payment-gateway",
                "rule": "deny-destructive-action",
            },
            {
                "decision": "require_approval",
                "target": "kubectl apply retry-policy",
                "rule": "approve-prod-write",
                "approvers": ["commerce-core", "change-manager"],
                "reasons": ["prod write"],
            },
        ]
        self.code = {
            "source": "ebpf.code_stack + REA",
            "repo": "demo/fintech",
            "iterations": [{"rca": {"finding": {"file": "payments.py", "line": 22}}}],
            "summary": {
                "fixed": 10,
                "iterations": 10,
                "candidate_attempts": 11,
                "rejected_candidates": 1,
            },
        }
        self.code_graph = {"summary": {"entities": 47, "relations": 76}}
        self.codeowners = OwnersIndex(Path(__file__).resolve().parent.parent)
        self.approvals = [
            {
                "request_id": "APR-test",
                "target": "kubectl apply retry-policy",
                "status": "pending",
                "approvers": ["commerce-core"],
            }
        ]

    def test_brief_has_actions_and_guards(self):
        brief = build_brief(
            self.snapshot,
            self.analysis,
            self.agents,
            self.policy,
            self.code,
            self.code_graph,
            codeowners=self.codeowners,
            approvals=self.approvals,
        )
        self.assertEqual(brief["status"], "mitigation_pending")
        self.assertIn("checkout-service", brief["headline"])
        priorities = [item["priority"] for item in brief["next_actions"]]
        self.assertIn("P0", priorities)
        self.assertTrue(any("APR-test" in item["action"] for item in brief["next_actions"]))
        self.assertTrue(any("不要执行" in item for item in brief["do_not"]))
        self.assertEqual(brief["code"]["fixed"], 10)
        self.assertEqual(brief["code"]["owner"], "@commerce-core")
        self.assertEqual(brief["provenance"]["service"]["mode"], "replay")
        self.assertTrue(brief["next_actions"][0]["check_result"]["status"] == "failed")

    def test_brief_markdown(self):
        brief = build_brief(self.snapshot, self.analysis)
        text = render_brief_markdown(brief)
        self.assertIn("# 值班决策简报", text)
        self.assertIn("下一步", text)
        self.assertIn("证据来源", text)


if __name__ == "__main__":
    unittest.main()
