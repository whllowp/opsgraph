"""Partial-observability RCA loop.

When eBPF covers only ~60% of the service graph, "not observed" must not be
treated as "healthy". This module infers from partial eBPF, APM metrics, logs,
config diffs and historical knowledge, then performs a safe recovery and
verifies it. It is deliberately separate from the full-coverage REA demo.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .partial_cases import (
    EDGES,
    INCIDENT,
    PROBES,
    RECOVERY,
    SERVICES,
)
from .util import write_json

HYPOTHESES = {
    "ledger_pool": {
        "service": "ledger-service",
        "title": "DB 连接池耗尽",
        "prior": 0.20,
    },
    "gateway_overload": {
        "service": "payment-gateway",
        "title": "支付网关自身过载",
        "prior": 0.25,
    },
    "checkout_retry": {
        "service": "checkout-service",
        "title": "checkout 重试放大",
        "prior": 0.20,
    },
    "network_latency": {
        "service": "payment-router",
        "title": "网络/下游延迟",
        "prior": 0.20,
    },
    "node_pressure": {
        "service": "node-3",
        "title": "节点资源压力",
        "prior": 0.15,
    },
}
KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "knowledge" / "failure_signatures.json"
)


def _normalise(raw: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in raw.values())
    if total <= 0:
        return raw
    return {key: round(max(0.0, value) / total, 6) for key, value in raw.items()}


def update_belief(prior: dict[str, float], likelihoods: dict[str, float]) -> dict[str, float]:
    return _normalise({key: prior[key] * float(likelihoods.get(key, 0.5)) for key in prior})


def coverage() -> dict[str, Any]:
    covered_services = [item for item in SERVICES if item["covered"]]
    covered_edges = [item for item in EDGES if item["covered"]]
    return {
        "services": len(SERVICES),
        "covered_services": len(covered_services),
        "service_pct": round(100 * len(covered_services) / len(SERVICES), 1),
        "edges": len(EDGES),
        "covered_edges": len(covered_edges),
        "edge_pct": round(100 * len(covered_edges) / len(EDGES), 1),
        "blind_services": [item["name"] for item in SERVICES if not item["covered"]],
    }


def _confidence(value: float) -> float:
    """60% coverage cannot justify an absolute 100% claim."""
    return round(min(0.99, value), 4)


def _rank(posterior: dict[str, float]) -> list[dict[str, Any]]:
    order = sorted(posterior.items(), key=lambda item: item[1], reverse=True)
    return [
        {
            "hypothesis": key,
            "service": HYPOTHESES[key]["service"],
            "title": HYPOTHESES[key]["title"],
            "probability": probability,
        }
        for key, probability in order
    ]


def load_knowledge(path: str | Path = KNOWLEDGE_PATH) -> dict[str, Any]:
    if not Path(path).exists():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return next(
        (
            item
            for item in payload.get("signatures") or []
            if item.get("id") == "db-pool-exhaustion"
        ),
        {},
    )


def run_partial_rea(iterations: int = 10) -> dict[str, Any]:
    budget = max(1, min(10, iterations))
    posterior = {key: value["prior"] for key, value in HYPOTHESES.items()}
    records: list[dict[str, Any]] = []
    coverage_info = coverage()
    knowledge = load_knowledge()
    after = RECOVERY["after"]
    for probe in PROBES:
        if len(records) >= min(budget, len(PROBES)):
            break
        posterior = update_belief(posterior, probe["likelihoods"])
        ranking = _rank(posterior)
        records.append(
            {
                "iteration": len(records) + 1,
                "phase": "probe",
                "probe_id": probe["id"],
                "action": probe["title"],
                "source": probe["source"],
                "cost": probe["cost"],
                "observations": probe["observations"],
                "posterior": posterior,
                "ranking": ranking,
                "top_hypothesis": ranking[0]["hypothesis"],
                "confidence": _confidence(ranking[0]["probability"]),
                "coverage": coverage_info,
                "knowledge_match": knowledge.get("id", "")
                if probe["id"] == "knowledge-match"
                else "",
            }
        )
    if len(records) < budget:
        ranking = _rank(posterior)
        before = RECOVERY["before"]
        recovered = (
            after["payment_gateway_p99_ms"] <= INCIDENT["slo_p99_ms"]
            and after["error_rate"] <= 0.01
        )
        records.append(
            {
                "iteration": len(records) + 1,
                "phase": "remediation",
                "action": RECOVERY["action"],
                "source": "config",
                "why": RECOVERY["why"],
                "before": before,
                "after": after,
                "recovered": recovered,
                "posterior": posterior,
                "ranking": ranking,
                "top_hypothesis": ranking[0]["hypothesis"],
                "confidence": _confidence(ranking[0]["probability"]),
                "coverage": coverage_info,
            }
        )
    if len(records) < budget:
        ranking = _rank(posterior)
        after = RECOVERY["after"]
        records.append(
            {
                "iteration": len(records) + 1,
                "phase": "verify",
                "action": "用 SLO 与错误率验证恢复",
                "source": "slo",
                "checks": [
                    {
                        "name": "payment-gateway P99 <= 800ms",
                        "actual": after["payment_gateway_p99_ms"],
                        "passed": after["payment_gateway_p99_ms"] <= INCIDENT["slo_p99_ms"],
                    },
                    {
                        "name": "error rate <= 1%",
                        "actual": after["error_rate"],
                        "passed": after["error_rate"] <= 0.01,
                    },
                ],
                "posterior": posterior,
                "ranking": ranking,
                "top_hypothesis": ranking[0]["hypothesis"],
                "confidence": _confidence(ranking[0]["probability"]),
                "coverage": coverage_info,
            }
        )
    if len(records) < budget:
        records.append(
            {
                "iteration": len(records) + 1,
                "phase": "coverage",
                "action": RECOVERY["coverage_fix"],
                "source": "coverage",
                "coverage_before": coverage_info,
                "coverage_after": {
                    **coverage_info,
                    "covered_services": coverage_info["covered_services"] + 1,
                    "service_pct": round(
                        100 * (coverage_info["covered_services"] + 1) / coverage_info["services"],
                        1,
                    ),
                    "blind_services": [
                        item for item in coverage_info["blind_services"] if item != "ledger-service"
                    ],
                },
                "residual_risk": "其余 3 个盲区服务仍没有进程内探针",
            }
        )
    while len(records) < budget:
        records.append(
            {
                "iteration": len(records) + 1,
                "phase": "observe",
                "action": "继续观察同一窗口",
                "source": "slo",
                "coverage": coverage_info,
            }
        )
    ranking = _rank(posterior)
    top = ranking[0]
    recovered = bool(
        (records and any(item.get("recovered") for item in records))
        or after["payment_gateway_p99_ms"] <= INCIDENT["slo_p99_ms"]
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "case_id": INCIDENT["case_id"],
        "title": INCIDENT["title"],
        "victim": INCIDENT["victim"],
        "slo_p99_ms": INCIDENT["slo_p99_ms"],
        "coverage": coverage_info,
        "knowledge": knowledge,
        "iterations": records,
        "summary": {
            "iterations": len(records),
            "root_service": top["service"],
            "root_cause": top["title"],
            "confidence": _confidence(top["probability"]),
            "recovered": recovered,
            "coverage_pct": coverage_info["service_pct"],
            "blind_spot": top["service"] in coverage_info["blind_services"],
            "slo_p99_ms": INCIDENT["slo_p99_ms"],
            "final_p99_ms": RECOVERY["after"]["payment_gateway_p99_ms"],
        },
        "recovery": RECOVERY,
    }


def render_partial_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    coverage_info = result.get("coverage") or {}
    lines = [
        "# 60% 覆盖下的 RCA / 恢复报告",
        "",
        f"> {result.get('title', '')}",
        "",
        f"- 根因：`{summary.get('root_service')}` / {summary.get('root_cause')}",
        f"- 置信度：{float(summary.get('confidence') or 0) * 100:.1f}%",
        f"- eBPF 覆盖：{coverage_info.get('service_pct')}% 服务 / {coverage_info.get('edge_pct')}% 边",
        f"- 盲区：{', '.join(coverage_info.get('blind_services') or [])}",
        f"- 恢复：{'PASS' if summary.get('recovered') else 'FAIL'} · P99 {summary.get('final_p99_ms')}ms",
        "",
        "## 十次迭代",
        "",
        "| # | 阶段 | 动作 | 来源 | 最高假设 | 置信度 |",
        "|---:|---|---|---|---|---:|",
    ]
    for item in result.get("iterations") or []:
        lines.append(
            f"| {item.get('iteration')} | {item.get('phase')} | {item.get('action')} | "
            f"{item.get('source')} | {item.get('top_hypothesis') or '-'} | "
            f"{float(item.get('confidence') or 0) * 100:.0f}% |"
        )
    lines += ["", "## 恢复", "", f"- {result.get('recovery', {}).get('action', '')}"]
    lines += ["", "## 证据缺口", ""]
    lines.append(
        "盲区服务没有进程内 eBPF，因此不能把缺失的 span 当成健康；"
        "本次通过 APM、日志、配置 diff 和知识签名完成推断。"
    )
    return "\n".join(lines) + "\n"


def write_partial_artifacts(result: dict[str, Any], root: str | Path) -> dict[str, str]:
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    json_path = write_json(target / "partial_rca.json", result)
    md_path = target / "partial-rca.md"
    md_path.write_text(render_partial_markdown(result), encoding="utf-8")
    return {"json": str(json_path), "report": str(md_path)}
