"""Historical Agent baselines and graph-grounded claim accuracy.

The current demo ships a small synthetic history file so the evaluation path is
runnable offline. In production this module should read persisted AgentSight
run summaries (one row per root session) from the same table that feeds the
graph.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from .config import DATA_DIR
from .util import read_json

DEFAULT_HISTORY_PATH = DATA_DIR / "history" / "agent_history.json"
METRICS = (
    "total_tokens",
    "cost_usd",
    "tool_calls",
    "failed_tool_calls",
    "retry_loops",
    "duration_s",
)


def load_history(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_HISTORY_PATH
    if not target.exists():
        return {"version": 1, "runs": []}
    data = read_json(target)
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"version": 1, "runs": []}
    return data


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _metric_summary(values: list[float], current: float) -> dict[str, Any]:
    if not values:
        return {
            "current": current,
            "median": None,
            "p95": None,
            "mean": None,
            "samples": 0,
            "deviation_pct": None,
        }
    median = _percentile(values, 0.5)
    p95 = _percentile(values, 0.95)
    deviation = ((current - median) / median * 100) if median else None
    return {
        "current": round(current, 4),
        "median": round(median, 4),
        "p95": round(p95, 4),
        "mean": round(mean(values), 4),
        "samples": len(values),
        "deviation_pct": round(deviation, 1) if deviation is not None else None,
    }


def _claim_accuracy(
    runs: list[dict[str, Any]],
    current_claims: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    by_agent: dict[str, dict[str, int]] = {}
    for run in runs:
        if run.get("claim_correct") is None:
            continue
        agent = str(run.get("agent_type") or "unknown")
        item = by_agent.setdefault(agent, {"correct": 0, "total": 0})
        item["total"] += 1
        if run.get("claim_correct"):
            item["correct"] += 1
    by_name = {str(item.get("name")): item for item in sessions}
    for claim in current_claims:
        status = claim.get("status")
        if status not in {"verified", "mismatch"}:
            continue
        name = str(claim.get("session") or "")
        session = by_name.get(name, {})
        agent = str(session.get("agent_type") or name.split(":", 1)[0] or "unknown")
        item = by_agent.setdefault(agent, {"correct": 0, "total": 0})
        item["total"] += 1
        if status == "verified":
            item["correct"] += 1
    rows = []
    total_correct = 0
    total = 0
    for agent, item in sorted(by_agent.items()):
        total_correct += item["correct"]
        total += item["total"]
        rows.append(
            {
                "agent": agent,
                "correct": item["correct"],
                "total": item["total"],
                "accuracy": round(item["correct"] / item["total"], 3) if item["total"] else None,
            }
        )
    return {
        "overall": {
            "correct": total_correct,
            "total": total,
            "accuracy": round(total_correct / total, 3) if total else None,
        },
        "by_agent": rows,
    }


def compute_baseline(history: dict[str, Any], agent_analysis: dict[str, Any]) -> dict[str, Any]:
    runs = [item for item in history.get("runs", []) if isinstance(item, dict)]
    sessions = [
        item for item in agent_analysis.get("sessions", []) if int(item.get("depth") or 0) == 0
    ]
    baseline_by_agent: dict[str, dict[str, Any]] = {}
    session_rows = []
    findings = []

    for session in sessions:
        agent = str(session.get("agent_type") or "unknown")
        historical = [item for item in runs if str(item.get("agent_type")) == agent]
        if agent not in baseline_by_agent:
            baseline_by_agent[agent] = {
                "agent": agent,
                "runs": len(historical),
            }
        metrics = {}
        for metric in METRICS:
            values = [float(item.get(metric) or 0) for item in historical]
            current = float(session.get(metric) or 0)
            summary = _metric_summary(values, current)
            metrics[metric] = summary
            p95 = summary["p95"]
            if p95 is not None:
                if metric in {"retry_loops", "failed_tool_calls"}:
                    spike = current > p95 + 0.5
                else:
                    spike = current > max(p95 * 1.2, p95 + 1)
                if spike:
                    findings.append(
                        {
                            "id": "agent-baseline-spike",
                            "severity": "warning",
                            "title": f"{metric} above historical p95",
                            "detail": (
                                f"{session.get('name')} {metric}={current:g} vs "
                                f"{agent} p95={p95:g} ({summary['samples']} historical runs)."
                            ),
                            "entities": [session.get("id")],
                            "session": session.get("name"),
                        }
                    )
        session_rows.append(
            {
                "key": session.get("key"),
                "name": session.get("name"),
                "agent_type": agent,
                "model": session.get("model"),
                "historical_runs": len(historical),
                "metrics": metrics,
            }
        )

    accuracy = _claim_accuracy(
        runs, agent_analysis.get("claims") or [], agent_analysis.get("sessions") or []
    )
    for row in accuracy["by_agent"]:
        if row["total"] >= 3 and (row["accuracy"] or 0) < 0.7:
            findings.append(
                {
                    "id": "agent-claim-accuracy-low",
                    "severity": "warning",
                    "title": f"{row['agent']} claim accuracy below 70%",
                    "detail": (
                        f"{row['correct']}/{row['total']} historical + current claims "
                        f"match graph RCA ({row['accuracy'] * 100:.0f}%)."
                    ),
                    "entities": [],
                    "session": row["agent"],
                }
            )

    return {
        "schema_version": 1,
        "history_path": Path(history.get("path") or "data/history/agent_history.json").name,
        "history_runs": len(runs),
        "history_window": {
            "first": min((str(item.get("timestamp")) for item in runs), default=""),
            "last": max((str(item.get("timestamp")) for item in runs), default=""),
        },
        "baseline_by_agent": list(baseline_by_agent.values()),
        "sessions": session_rows,
        "claim_accuracy": accuracy,
        "findings": findings,
    }
