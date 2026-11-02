"""Pre-execution policy gate for risky agent actions.

The gate is deliberately graph-grounded: the same action can be allowed in a
sandbox and denied during an active incident, because the decision reads the
service's status, criticality, RCA role, and the agent's current risk/claim
state from the materialized graph.

It is a policy *engine*, not an auto-remediator. ``require_approval`` means a
human (or a separate approval workflow) must decide before the tool runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent_graph import _tool_risk
from .agentsight import ToolCall

POLICY_VERSION = "2026-11-02.1"
READ_ONLY_TOOLS = {
    "query_umodel",
    "read_file",
    "search",
    "kubectl_get",
    "verify_slo",
    "list_files",
    "grep",
    "git_status",
    "git_diff",
}
WRITE_TOOLS = {
    "apply_patch",
    "write_file",
    "edit_file",
    "kubectl_apply",
    "kubectl_patch",
    "kubectl_rollout",
    "kubectl_delete",
    "terraform_apply",
    "helm_upgrade",
}
DESTRUCTIVE_TOKENS = (
    "kubectl delete",
    "kubectl drain",
    "rm -rf",
    "drop table",
    "truncate ",
    "terraform destroy",
    "helm uninstall",
    "kubectl scale --replicas=0",
)
PROD_TOKENS = ("prod", "production", "-n payments", "-n shop", "payments", "checkout")


@dataclass
class GateDecision:
    session: str
    tool: str
    target: str
    risk: str
    decision: str
    rule: str
    reasons: list[str] = field(default_factory=list)
    service: str = ""
    incident: str = ""
    approvers: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    policy_version: str = POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "tool": self.tool,
            "target": self.target,
            "risk": self.risk,
            "decision": self.decision,
            "rule": self.rule,
            "reasons": self.reasons,
            "service": self.service,
            "incident": self.incident,
            "approvers": self.approvers,
            "evidence": self.evidence,
            "policy_version": self.policy_version,
        }


def _session_for(agents: dict[str, Any], session_key: str) -> dict[str, Any]:
    for session in agents.get("sessions", []):
        if session.get("key") == session_key or session.get("name") == session_key:
            return session
    return {}


def _service_for(snapshot: dict[str, Any], target: str, linked: str) -> dict[str, Any]:
    services = [
        item
        for item in snapshot.get("entities", [])
        if item.get("__entity_type__") == "svc.service"
    ]
    for service in services:
        name = str(service.get("display_name") or "")
        if name and name.lower() in target.lower():
            return service
    if linked:
        for service in services:
            if str(service.get("display_name") or "") == linked:
                return service
    return {}


def _incident_for(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(
        (
            item
            for item in snapshot.get("entities", [])
            if item.get("__entity_type__") == "ops.incident"
        ),
        {},
    )


def _is_destructive(tool_name: str, target: str, command: str) -> bool:
    text = f"{tool_name} {target} {command}".lower()
    return any(token in text for token in DESTRUCTIVE_TOKENS)


def _is_write(tool_name: str) -> bool:
    return tool_name.lower() in WRITE_TOOLS


def _is_read_only(tool_name: str) -> bool:
    return tool_name.lower() in READ_ONLY_TOOLS


def _looks_prod(target: str, command: str, service: dict[str, Any]) -> bool:
    text = f"{target} {command}".lower()
    if any(token in text for token in PROD_TOKENS):
        return True
    return str(service.get("criticality") or "").lower() in {"platinum", "gold"}


def _approvers(service: dict[str, Any], decision: str) -> list[str]:
    if decision == "deny":
        return []
    owner = str(service.get("owner") or "").strip()
    result = [owner] if owner else []
    if "payment" in str(service.get("display_name") or "").lower():
        result.append("payments-oncall")
    result.append("change-manager")
    seen = []
    for item in result:
        if item and item not in seen:
            seen.append(item)
    return seen


def evaluate_gate(
    snapshot: dict[str, Any],
    agents: dict[str, Any],
    session_key: str,
    tool_name: str,
    target: str,
    command: str = "",
) -> dict[str, Any]:
    session = _session_for(agents, session_key)
    linked = str(session.get("linked_service") or "")
    service = _service_for(snapshot, target, linked)
    incident = _incident_for(snapshot)
    incident_active = str(incident.get("status") or "").lower() in {
        "investigating",
        "open",
        "mitigating",
    } or str(service.get("status") or "").lower() in {"critical", "warning"}

    temp = ToolCall(
        id="proposed",
        session_key=session_key,
        timestamp_ms=0,
        tool_name=tool_name,
        status="proposed",
        duration_ms=None,
        input={"command": command} if command else {},
        output=None,
        target=target,
        related_pid=None,
    )
    risk = _tool_risk(temp)
    service_name = str(service.get("display_name") or "")
    service_status = str(service.get("status") or "unknown")
    criticality = str(service.get("criticality") or "unknown")
    role = str(service.get("role") or "")
    prod = _looks_prod(target, command, service)
    risk_score = float(session.get("risk_score") or 0)

    reasons: list[str] = []
    if service_name:
        reasons.append(
            f"target maps to {service_name} ({service_status}, {criticality}, role={role or 'none'})"
        )
    if incident_active:
        reasons.append(f"incident {incident.get('display_name', 'active')} is still active")
    if risk_score >= 60:
        reasons.append(f"agent session risk is {risk_score:.0f}/100")
    if session.get("claim_status") == "mismatch":
        reasons.append("agent claim conflicts with graph RCA")

    if _is_destructive(tool_name, target, command):
        decision = GateDecision(
            session=str(session.get("name") or session_key),
            tool=tool_name,
            target=target,
            risk="high",
            decision="deny",
            rule="deny-destructive-action",
            reasons=[
                "action is destructive and cannot run unattended",
                *reasons,
            ],
            service=service_name,
            incident=str(incident.get("display_name") or ""),
            evidence={
                "service_status": service_status,
                "criticality": criticality,
                "incident_active": incident_active,
                "policy": "destructive actions are never auto-approved",
            },
        )
        return decision.as_dict()

    if _is_write(tool_name) and (prod or incident_active):
        decision = GateDecision(
            session=str(session.get("name") or session_key),
            tool=tool_name,
            target=target,
            risk="high",
            decision="require_approval",
            rule="approve-prod-write-during-incident" if incident_active else "approve-prod-write",
            reasons=[
                "write action on a production/critical surface needs a human approver",
                *reasons,
            ],
            service=service_name,
            incident=str(incident.get("display_name") or ""),
            approvers=_approvers(service, "require_approval"),
            evidence={
                "service_status": service_status,
                "criticality": criticality,
                "incident_active": incident_active,
                "recommended_scope": "single service, single change window",
            },
        )
        return decision.as_dict()

    if _is_write(tool_name) or risk == "high":
        decision = GateDecision(
            session=str(session.get("name") or session_key),
            tool=tool_name,
            target=target,
            risk=risk,
            decision="require_approval",
            rule="approve-high-risk-write",
            reasons=["high-risk write requires an approver", *reasons],
            service=service_name,
            incident=str(incident.get("display_name") or ""),
            approvers=_approvers(service, "require_approval"),
            evidence={"incident_active": incident_active},
        )
        return decision.as_dict()

    if _is_read_only(tool_name):
        decision = GateDecision(
            session=str(session.get("name") or session_key),
            tool=tool_name,
            target=target,
            risk="low",
            decision="allow",
            rule="allow-read-only",
            reasons=["read-only investigation is allowed", *reasons],
            service=service_name,
            incident=str(incident.get("display_name") or ""),
            evidence={"incident_active": incident_active},
        )
        return decision.as_dict()

    if risk_score >= 60 and risk in {"medium", "high"}:
        decision = GateDecision(
            session=str(session.get("name") or session_key),
            tool=tool_name,
            target=target,
            risk=risk,
            decision="require_approval",
            rule="approve-high-risk-agent",
            reasons=["high-risk agent performing a state-changing action", *reasons],
            service=service_name,
            incident=str(incident.get("display_name") or ""),
            approvers=_approvers(service, "require_approval"),
            evidence={"agent_risk": risk_score},
        )
        return decision.as_dict()

    decision = GateDecision(
        session=str(session.get("name") or session_key),
        tool=tool_name,
        target=target,
        risk=risk,
        decision="allow",
        rule="allow-default",
        reasons=["no blocking policy matched; keep monitoring", *reasons],
        service=service_name,
        incident=str(incident.get("display_name") or ""),
        evidence={"incident_active": incident_active},
    )
    return decision.as_dict()


def demo_gate_evaluations(
    snapshot: dict[str, Any], agents: dict[str, Any], session_key: str = "sre-claude-01"
) -> list[dict[str, Any]]:
    cases = [
        (
            "kubectl_delete",
            "kubectl delete deploy/payment-gateway -n payments",
            "delete the victim deployment",
        ),
        (
            "kubectl_apply",
            "kubectl apply -f checkout-retry-policy.yaml -n shop",
            "apply the retry policy",
        ),
        (
            "query_umodel",
            "payment-gateway upstream",
            "read-only RCA query",
        ),
    ]
    results = []
    for tool, target, label in cases:
        result = evaluate_gate(snapshot, agents, session_key, tool, target, target)
        result["label"] = label
        results.append(result)
    return results
