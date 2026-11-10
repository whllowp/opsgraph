import json
import unittest

from opsgraph.brain import apply_brain
from opsgraph.laya import LayaModel
from opsgraph.llm import LLMBrain


def service_group() -> dict:
    return {
        "severity": "P1",
        "root_service": "checkout-service",
        "member_count": 10,
        "inhibited_count": 6,
        "members": [
            {"target": "checkout-service", "target_type": "svc.service", "severity": "P1"},
            {"target": "payment-gateway", "target_type": "svc.service", "severity": "P2"},
        ],
        "rca": {
            "root_cause": "checkout-service",
            "confidence": 0.8,
            "code_fixes": 10,
        },
    }


def node_group() -> dict:
    return {
        "severity": "P3",
        "root_service": "",
        "member_count": 5,
        "inhibited_count": 0,
        "members": [{"target": "node-3", "target_type": "infra.node", "severity": "P3"}],
        "rca": {"root_cause": "", "confidence": 0.0, "code_fixes": 0},
    }


def analysis() -> dict:
    return {
        "ranking": [
            {"service": "checkout-service", "confidence": 0.8},
            {"service": "payment-gateway", "confidence": 0.4},
        ]
    }


class LayaTests(unittest.TestCase):
    def test_service_is_paged_and_node_is_low_confidence(self):
        model = LayaModel()
        service = model.decide(service_group())
        node = model.decide(node_group())
        self.assertEqual(service["action"], "page")
        self.assertGreater(service["root_probability"], 0.8)
        self.assertIn(node["action"], {"suppress", "watch"})
        self.assertGreater(node["false_alarm_probability"], 0.5)

    def test_llm_guardrail_blocks_p1_suppression(self):
        def transport(url, headers, payload):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "root_cause": "checkout-service",
                                    "confidence": 0.9,
                                    "false_alarm_probability": 0.1,
                                    "action": "suppress",
                                    "reason": "looks fine",
                                    "citations": ["graph"],
                                }
                            )
                        }
                    }
                ]
            }

        llm = LLMBrain(url="http://fake", api_key="test", transport=transport)
        result = apply_brain([service_group()], analysis(), llm=llm)
        self.assertEqual(result["groups"], 1)
        decision = service_group()
        apply_brain([decision], analysis(), llm=llm)
        self.assertEqual(decision["decision"]["source"], "laya")
        self.assertEqual(decision["decision"]["llm"]["status"], "guardrailed")

    def test_valid_llm_confirmation(self):
        def transport(url, headers, payload):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "root_cause": "checkout-service",
                                    "confidence": 0.85,
                                    "false_alarm_probability": 0.12,
                                    "action": "investigate",
                                    "reason": "graph and alert agree",
                                    "citations": ["graph"],
                                }
                            )
                        }
                    }
                ]
            }

        group = service_group()
        llm = LLMBrain(url="http://fake", api_key="test", transport=transport)
        result = apply_brain([group], analysis(), llm=llm)
        self.assertEqual(result["groups"], 1)
        self.assertEqual(group["decision"]["source"], "laya+llm")
        self.assertEqual(group["decision"]["action"], "investigate")


if __name__ == "__main__":
    unittest.main()
