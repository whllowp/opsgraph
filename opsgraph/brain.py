"""Alert brain: Laya decision model + optional LLM proposer.

Laya is the deterministic baseline. The LLM can enrich/confirm it, but is
validated against graph candidates and guardrails; it can never suppress a P1
root alert while graph evidence says the root probability is high.
"""

from __future__ import annotations

from typing import Any

from .laya import LayaModel
from .llm import LLMBrain


def _candidates(groups: list[dict[str, Any]], analysis: dict[str, Any]) -> list[str]:
    names = [str(item.get("service")) for item in analysis.get("ranking") or []]
    for group in groups:
        if group.get("root_service"):
            names.append(str(group["root_service"]))
        for member in group.get("members") or []:
            if str(member.get("target_type")) == "svc.service":
                names.append(str(member.get("target")))
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def apply_brain(
    groups: list[dict[str, Any]],
    analysis: dict[str, Any],
    llm: LLMBrain | None = None,
    laya: LayaModel | None = None,
) -> dict[str, Any]:
    model = laya or LayaModel()
    brain = llm or LLMBrain()
    candidates = _candidates(groups, analysis)
    for group in groups:
        base = model.decide(group)
        rca = group.get("rca") or {}
        graph_root = str(rca.get("root_cause") or "")
        graph_confidence = float(rca.get("confidence") or 0)
        decision = {
            "source": "laya",
            "action": base["action"],
            "root_cause": graph_root or str((group.get("root_service") or "")),
            "root_probability": base["root_probability"],
            "false_alarm_probability": base["false_alarm_probability"],
            "confidence": base["root_probability"],
            "reasons": base["reasons"],
            "expected_costs": base["expected_costs"],
            "guardrail": base["guardrail"],
            "llm": brain.status(),
            "laya": base,
        }
        proposal = brain.propose(group, candidates)
        if proposal.get("status") == "ok":
            safe = True
            guardrail = ""
            proposed_root = str(proposal.get("root_cause") or "")
            if graph_root and proposed_root != graph_root and graph_confidence >= 0.6:
                safe = False
                guardrail = "LLM root disagrees with high-confidence graph root"
            if proposal.get("action") == "suppress" and (
                str(group.get("severity")) == "P1" or base["root_probability"] >= 0.4
            ):
                safe = False
                guardrail = "LLM cannot suppress a high-probability P1 root"
            if safe:
                decision.update(
                    {
                        "source": "laya+llm",
                        "action": proposal["action"],
                        "root_cause": proposed_root or decision["root_cause"],
                        "false_alarm_probability": max(
                            base["false_alarm_probability"],
                            float(proposal.get("false_alarm_probability") or 0),
                        ),
                        "confidence": round(
                            (base["root_probability"] + float(proposal.get("confidence") or 0)) / 2,
                            4,
                        ),
                        "reasons": [*base["reasons"], proposal.get("reason", "")],
                        "llm": {
                            **proposal,
                            "guardrail": guardrail,
                        },
                    }
                )
            else:
                decision["llm"] = {**proposal, "status": "guardrailed", "guardrail": guardrail}
        else:
            decision["llm"] = proposal
        group["decision"] = decision
    actions: dict[str, int] = {}
    for group in groups:
        action = str((group.get("decision") or {}).get("action") or "unknown")
        actions[action] = actions.get(action, 0) + 1
    return {
        "laya": {"model": "laya", "config": str(model.path)},
        "llm": brain.status(),
        "actions": actions,
        "groups": len(groups),
    }
