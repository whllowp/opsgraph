import tempfile
import unittest
from pathlib import Path

from opsgraph.approvals import ApprovalStore
from opsgraph.brief_history import ActionLedger
from opsgraph.codeowners import OwnersIndex
from opsgraph.verification import evaluate_check


def snapshot(p99: float) -> dict:
    return {
        "window": {"end_iso": "2026-11-02T01:00:00+08:00"},
        "entities": [
            {
                "__entity_type__": "svc.service",
                "display_name": "payment-gateway",
                "latency_p99_ms": p99,
                "slo_p99_ms": 800,
            }
        ],
    }


class VerificationTests(unittest.TestCase):
    def test_slo_check_closes_only_after_recovery(self):
        check = {
            "kind": "slo",
            "service": "payment-gateway",
            "metric": "latency_p99_ms",
            "operator": "<=",
            "target": 800,
        }
        bad = evaluate_check(check, snapshot(2255))
        good = evaluate_check(check, snapshot(650))
        self.assertEqual(bad["status"], "failed")
        self.assertEqual(good["status"], "passed")

    def test_approval_check(self):
        check = {"kind": "approval", "request_id": "APR-1"}
        self.assertEqual(evaluate_check(check, snapshot(1), approvals=[])["status"], "pending")
        approved = [{"request_id": "APR-1", "status": "approved"}]
        self.assertEqual(evaluate_check(check, snapshot(1), approvals=approved)["status"], "passed")

    def test_ledger_auto_closes_and_reopens(self):
        action = {
            "priority": "P0",
            "action": "verify payment-gateway SLO",
            "owner": "payments-backend",
            "because": "agent remediation is pending",
            "verify": "P99 <= 800ms",
            "check": {
                "kind": "slo",
                "service": "payment-gateway",
                "metric": "latency_p99_ms",
                "operator": "<=",
                "target": 800,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ActionLedger(Path(tmp) / "history.jsonl")
            brief = {"next_actions": [action], "novice": {}}
            first = ledger.reconcile(brief, snapshot(2255))
            self.assertEqual(first["next_actions"][0]["status"], "open")
            second = ledger.reconcile({"next_actions": [action], "novice": {}}, snapshot(700))
            self.assertEqual(second["next_actions"][0]["status"], "closed")
            self.assertEqual(second["status"], "verified")
            self.assertTrue(any(e["event"] == "closed" for e in second["history"]))
            third = ledger.reconcile({"next_actions": [action], "novice": {}}, snapshot(900))
            self.assertEqual(third["next_actions"][0]["status"], "open")
            self.assertTrue(any(e["event"] == "reopened" for e in third["history"]))


class OwnershipAndApprovalTests(unittest.TestCase):
    def test_codeowners_route(self):
        owners = OwnersIndex(Path(__file__).resolve().parent.parent)
        self.assertEqual(owners.owner_for("demo/fintech/payments.py"), "@commerce-core")
        self.assertEqual(owners.owner_for("opsgraph/brief.py"), "@opsgraph-core")

    def test_approval_ticket_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ApprovalStore(Path(tmp) / "approvals.json")
            policy = [
                {
                    "decision": "require_approval",
                    "target": "kubectl apply retry-policy",
                    "rule": "approve-prod-write",
                    "approvers": ["commerce-core"],
                }
            ]
            created = store.ensure_from_policy(policy)
            self.assertEqual(len(created), 1)
            request_id = created[0]["request_id"]
            decided = store.decide(request_id, "approved", "cto", "checked")
            self.assertEqual(decided["status"], "approved")
            self.assertEqual(store.summary()["by_status"]["approved"], 1)


if __name__ == "__main__":
    unittest.main()
