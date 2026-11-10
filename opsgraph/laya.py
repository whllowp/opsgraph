"""Laya: an explainable alert-triage decision model.

Laya treats an alert group as evidence for two hypotheses:

* ``root``: the group contains the actual service root cause;
* ``noise``: the group is a red herring, node-only symptom or duplicate.

It computes a log-odds score (sigmoid -> probability) and chooses the action
with minimum expected cost. Every number is explainable and configurable in
``config/laya_model.json``. The model never executes remediation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

DEFAULT_PATH = PROJECT_ROOT / "config" / "laya_model.json"


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


class LayaModel:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.config = json.loads(self.path.read_text(encoding="utf-8"))

    def features(self, group: dict[str, Any]) -> dict[str, float]:
        members = group.get("members") or []
        rca = group.get("rca") or {}
        total = max(1, len(members))
        inhibited = float(group.get("inhibited_count") or 0) / total
        service_count = sum(1 for item in members if str(item.get("target_type")) == "svc.service")
        node_count = sum(1 for item in members if str(item.get("target_type")) == "infra.node")
        return {
            "confidence": float(rca.get("confidence") or 0),
            "service_alert": 1.0 if service_count else 0.0,
            "severity_p1": 1.0 if str(group.get("severity")) == "P1" else 0.0,
            "node_only": 1.0 if node_count and not service_count else 0.0,
            "inhibited_ratio": min(1.0, inhibited),
            "code_fix": 1.0 if int(rca.get("code_fixes") or 0) > 0 else 0.0,
        }

    def probability(self, features: dict[str, float]) -> float:
        weights = self.config.get("weights") or {}
        score = float(self.config.get("intercept") or 0)
        for name, value in features.items():
            score += float(weights.get(name) or 0) * value
        return _sigmoid(score)

    def expected_costs(self, probability: float) -> dict[str, float]:
        costs = self.config.get("costs") or {}
        result = {}
        for action, item in costs.items():
            miss = float(item.get("miss") or 0) * (1 - probability)
            false_page = float(item.get("false_page") or 0) * probability
            result[action] = round(miss + false_page, 4)
        return result

    def decide(self, group: dict[str, Any]) -> dict[str, Any]:
        features = self.features(group)
        probability = self.probability(features)
        costs = self.expected_costs(probability)
        action = min(costs, key=costs.get) if costs else "investigate"
        guardrail = ""
        thresholds = self.config.get("guardrails") or {}
        floor = float(thresholds.get("p1_suppress_probability") or 0.4)
        if action == "suppress" and str(group.get("severity")) == "P1" and probability >= floor:
            action = "investigate"
            guardrail = "P1 cannot be suppressed while root probability is above the guardrail"
        if features["node_only"] and action in {"page", "investigate"}:
            action = "watch"
            guardrail = "node-only evidence cannot page a service root; watch/correlate instead"
        reasons = [
            f"graph confidence {features['confidence']:.2f}",
            f"inhibited ratio {features['inhibited_ratio']:.2f}",
        ]
        if features["node_only"]:
            reasons.append("node-only alert; no service root evidence")
        if features["severity_p1"]:
            reasons.append("P1 group")
        if features["code_fix"]:
            reasons.append("code fix is available")
        return {
            "source": "laya",
            "model": "laya",
            "root_probability": round(probability, 4),
            "false_alarm_probability": round(1 - probability, 4),
            "action": action,
            "expected_costs": costs,
            "features": features,
            "guardrail": guardrail,
            "reasons": reasons,
        }
