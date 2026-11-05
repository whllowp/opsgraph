"""Operator brief: turn graph evidence into a decision, not another dashboard.

This is the product layer above RCA/AgentSight/code-RCA. It answers five
questions for an on-call engineer or a novice:

1. what is broken?
2. why do we believe it?
3. what did the AI agent change, and is it verified?
4. what should I do next, and who owns it?
5. what is real (live/replay) in this evidence?

The brief is deterministic and reads only materialized artifacts. It never
invents a root cause; every action links back to a graph/agent/code fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _service(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in snapshot.get("entities", [])
            if item.get("__entity_type__") == "svc.service" and item.get("display_name") == name
        ),
        {},
    )


def _incident(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(
        (
            item
            for item in snapshot.get("entities", [])
            if item.get("__entity_type__") == "ops.incident"
        ),
        {},
    )


def _action(
    priority: str,
    action: str,
    owner: str,
    because: str,
    verify: str,
) -> dict[str, str]:
    return {
        "priority": priority,
        "action": action,
        "owner": owner,
        "because": because,
        "verify": verify,
    }


def build_brief(
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    agents: dict[str, Any] | None = None,
    policy: list[dict[str, Any]] | None = None,
    code_rca: dict[str, Any] | None = None,
    code_graph: dict[str, Any] | None = None,
    umodel_info: dict[str, Any] | None = None,
    real_perf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agents = agents or {}
    policy = policy or []
    code_rca = code_rca or {}
    code_graph = code_graph or {}
    incident = _incident(snapshot)
    victim_name = str(analysis.get("victim") or incident.get("victim") or "")
    root = str(analysis.get("root_cause") or incident.get("root_cause") or "")
    victim = _service(snapshot, victim_name)
    root_service = _service(snapshot, root)
    ranking = analysis.get("ranking") or []
    top = ranking[0] if ranking else {}
    reasons = list(top.get("reasons") or [])[:3]
    p99 = float(victim.get("latency_p99_ms") or 0)
    baseline = float(victim.get("baseline_p99_ms") or 0)
    delta = float(victim.get("p99_delta_pct") or 0)
    error_rate = float(victim.get("error_rate") or 0)
    confidence = float(analysis.get("confidence") or 0)

    sessions = agents.get("sessions") or []
    high_risk_sessions = [item for item in sessions if str(item.get("risk_level")) == "high"]
    pending = [
        item for item in sessions if str(item.get("remediation_status")) == "pending_validation"
    ]
    high_risk_effects = int((agents.get("summary") or {}).get("high_risk_effects") or 0)
    claim_verified = int((agents.get("summary") or {}).get("claims_verified") or 0)
    claim_mismatch = int((agents.get("summary") or {}).get("claims_mismatched") or 0)
    actions: list[dict[str, str]] = []
    do_not: list[str] = []

    deny = [item for item in policy if item.get("decision") == "deny"]
    approvals = [item for item in policy if item.get("decision") == "require_approval"]
    if deny:
        do_not.append(f"不要执行 `{deny[0].get('target')}`（{deny[0].get('rule')}）")
    if pending:
        owner = str(victim.get("owner") or "on-call")
        actions.append(
            _action(
                "P0",
                f"验证 Agent 对 `{pending[0].get('linked_service') or root}` 的处置是否真正恢复了 SLO",
                owner,
                "Agent 已执行 rollout，但同窗口 eBPF 仍显示服务 critical/warning；"
                "`remediation_status=pending_validation`。",
                "P99 回到基线附近、错误率回落，且连续一个窗口无新告警。",
            )
        )
    if high_risk_effects:
        actions.append(
            _action(
                "P1",
                "复核 Agent 的高风险变更并补齐审批记录",
                "change-manager",
                f"Agent 产生 {high_risk_effects} 个高风险系统效果；"
                f"claim verified={claim_verified}, mismatch={claim_mismatch}。",
                "每个 prod 变更都有审批人、变更单和可回滚版本。",
            )
        )
    if approvals:
        first = approvals[0]
        actions.append(
            _action(
                "P0",
                f"对 `{first.get('target')}` 发起审批后再执行",
                ", ".join(first.get("approvers") or ["change-manager"]),
                f"门禁命中 `{first.get('rule')}`：{'; '.join(first.get('reasons') or [])}",
                "审批通过 + 只影响目标服务 + 执行后 eBPF 复测。",
            )
        )
    code_summary = code_rca.get("summary") or {}
    code_fixed = int(code_summary.get("fixed") or 0)
    code_iterations = int(code_summary.get("iterations") or 0)
    if code_fixed:
        actions.append(
            _action(
                "P1",
                f"把 {code_fixed} 个代码级修复纳入发布计划",
                str(root_service.get("owner") or "commerce-core"),
                f"REA 在 {code_iterations} 个故障上完成补丁、回归与复测；"
                f"UModel 代码图 {int((code_graph.get('summary') or {}).get('entities') or 0)} 个实体。",
                "代码回归 PASS + canary 指标稳定 + 回滚包就绪。",
            )
        )
    if not actions:
        actions.append(
            _action(
                "P0",
                "确认根因假设并做单变量验证",
                str(root_service.get("owner") or "on-call"),
                "当前没有 Agent 处置或代码补丁动作；先用图查询验证根因。",
                "被怀疑的变更回滚后，P99/错误率在同一窗口回落。",
            )
        )
    actions.sort(key=lambda item: item["priority"])
    actions = actions[:4]

    status = "mitigation_pending" if pending else "investigating"
    headline = (
        f"{incident.get('display_name') or '生产故障'}：根因 {root or '待确认'}"
        f"（{confidence * 100:.0f}%），受害 {victim_name or '待确认'}"
        f"{'；Agent 处置尚未验证' if pending else ''}。"
    )
    what = (
        f"{victim_name or '受害服务'} P99 {p99:.0f}ms"
        + (f"（基线 {baseline:.0f}ms，+{delta:.0f}%）" if baseline else "")
        + f"，错误率 {error_rate * 100:.2f}%，QPS {float(victim.get('qps') or 0):.0f}。"
    )
    why = reasons or [analysis.get("summary") or "图证据不足，需要继续排查。"]
    now = (
        "先做 P0 的验证动作，再决定是否继续变更；不要同时改多个变量。"
        if pending
        else "先按 P0 动作验证根因；高风险变更必须过门禁审批。"
    )
    provenance = {
        "service": {
            "source": "deepflow.l7_flow_log",
            "mode": "replay",
            "window": snapshot.get("window") or {},
        },
        "agents": {
            "source": agents.get("source") or "agentsight.snapshot",
            "mode": "replay",
            "sessions": len(sessions),
        },
        "code": {
            "source": code_rca.get("source") or "ebpf.code_stack + REA",
            "mode": "replay + real perf calibration",
            "iterations": code_iterations,
            "real_perf": (real_perf or {}).get("summary") or "",
        },
        "umodel": umodel_info or {},
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "severity": incident.get("severity") or "",
        "headline": headline,
        "what": what,
        "why": why,
        "agent": {
            "high_risk_sessions": [item.get("name") for item in high_risk_sessions],
            "high_risk_effects": high_risk_effects,
            "claim_verified": claim_verified,
            "claim_mismatch": claim_mismatch,
            "pending_remediation": [item.get("name") for item in pending],
        },
        "code": {
            "fixed": code_fixed,
            "iterations": code_iterations,
            "candidates": int(code_summary.get("candidate_attempts") or 0),
            "rejected": int(code_summary.get("rejected_candidates") or 0),
            "entities": int((code_graph.get("summary") or {}).get("entities") or 0),
        },
        "next_actions": actions,
        "do_not": do_not,
        "evidence": [
            f".entity with(domain='svc', name='svc.service', query='{victim_name}')"
            if victim_name
            else "",
            ".entity with(domain='ai', name='ai.agent_session') | sort risk_score desc"
            if sessions
            else "",
            ".entity with(domain='code', name='code.issue') | sort p99_before_ms desc"
            if code_fixed
            else "",
        ],
        "provenance": provenance,
        "novice": {
            "what": what,
            "why": " ".join(str(item) for item in why[:2]),
            "now": now,
        },
        "headline_short": (
            f"{root or '待确认'} → {victim_name or '待确认'}"
            f" · {'处置待验证' if pending else '排查中'}"
        ),
    }


def render_brief_markdown(brief: dict[str, Any]) -> str:
    lines = [
        "# 值班决策简报",
        "",
        f"> {brief.get('headline', '')}",
        "",
        f"- 状态：**{brief.get('status')}**",
        f"- 严重级别：{brief.get('severity') or '-'}",
        "",
        "## 发生了什么",
        "",
        brief.get("what", ""),
        "",
        "## 为什么",
        "",
    ]
    for reason in brief.get("why") or []:
        lines.append(f"- {reason}")
    lines += ["", "## 下一步", ""]
    for action in brief.get("next_actions") or []:
        lines.append(
            f"- **[{action['priority']}] {action['action']}**  "
            f"owner: `{action['owner']}`  \n"
            f"  依据：{action['because']}  \n"
            f"  验证：{action['verify']}"
        )
    if brief.get("do_not"):
        lines += ["", "## 不要做", ""]
        for item in brief["do_not"]:
            lines.append(f"- {item}")
    lines += ["", "## 证据来源", ""]
    for key, item in (brief.get("provenance") or {}).items():
        lines.append(f"- `{key}`：{item}")
    lines += ["", "## 小白版", ""]
    for key in ("what", "why", "now"):
        lines.append(f"- **{key}**：{brief['novice'].get(key, '')}")
    return "\n".join(lines) + "\n"
