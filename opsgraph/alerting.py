"""Smart alert policy engine: evaluate, dedupe, group, inhibit and RCA.

The engine is deliberately deterministic: the demo consumes the same
DeepFlow-shaped snapshot as the graph, evaluates policies against service/node
metrics, then compresses an alert storm into a small number of root-aligned
groups. It never invents a metric; every event carries its policy and value.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .util import write_json

DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "alert_policies.json"
SEVERITY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5}
METRIC_LABEL = {
    "slo_burn_rate": "SLO burn",
    "latency_ratio": "P99 / baseline",
    "error_rate": "error rate",
    "cpu_utilization": "CPU",
}


def load_policies(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_POLICY_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat()


def _eid(*parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(item) for item in parts).encode("utf-8")).hexdigest()[:10]
    return "ALT-" + digest


def _service_metrics(service: dict[str, Any]) -> dict[str, float]:
    p99 = float(service.get("latency_p99_ms") or 0)
    baseline = float(service.get("baseline_p99_ms") or 0)
    slo = float(service.get("slo_p99_ms") or 0)
    return {
        "slo_burn_rate": p99 / slo if slo and p99 > slo else 0.0,
        "latency_ratio": p99 / baseline if baseline else 0.0,
        "error_rate": float(service.get("error_rate") or 0),
    }


def _node_metrics(node: dict[str, Any]) -> dict[str, float]:
    return {"cpu_utilization": float(node.get("cpu_utilization") or 0)}


def _value(policy: dict[str, Any], target: dict[str, Any], target_type: str) -> float:
    metrics = _node_metrics(target) if target_type == "infra.node" else _service_metrics(target)
    return float(metrics.get(str(policy.get("metric") or ""), 0))


def _passes(policy: dict[str, Any], value: float) -> bool:
    threshold = float(policy.get("threshold") or 0)
    operator = str(policy.get("operator") or ">=")
    return value >= threshold if operator == ">=" else value > threshold


def _event(
    policy: dict[str, Any],
    target: str,
    target_type: str,
    value: float,
    fired_ts: int,
    seq: int,
    source: str,
    labels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": _eid(policy["id"], target, fired_ts, seq),
        "policy_id": policy["id"],
        "policy_name": policy["name"],
        "target": target,
        "target_type": target_type,
        "severity": policy.get("severity", "P3"),
        "status": "firing",
        "metric": policy.get("metric", ""),
        "value": round(value, 4),
        "threshold": policy.get("threshold", 0),
        "fired_ts": fired_ts,
        "fired_at": _iso(fired_ts),
        "labels": labels or {},
        "summary": f"{target} {METRIC_LABEL.get(policy.get('metric', ''), policy.get('metric', ''))}={value:.3f}",
        "source": source,
    }


def _generated_events(
    snapshot: dict[str, Any],
    policies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = int((snapshot.get("window") or {}).get("start") or 0)
    entities = snapshot.get("entities") or []
    services = [item for item in entities if item.get("__entity_type__") == "svc.service"]
    nodes = [item for item in entities if item.get("__entity_type__") == "infra.node"]
    events: list[dict[str, Any]] = []
    for policy in policies:
        if not policy.get("enabled", True):
            continue
        index = 10
        if policy.get("target_type") == "infra.node":
            for node in nodes:
                value = _value(policy, node, "infra.node")
                if _passes(policy, value):
                    for offset in range(12):
                        ts = start + (index + offset) * 60
                        events.append(
                            _event(
                                policy,
                                str(node.get("display_name")),
                                "infra.node",
                                value,
                                ts,
                                offset,
                                "replay.deepflow",
                                {"node": node.get("display_name")},
                            )
                        )
        else:
            for service in services:
                value = _value(policy, service, "svc.service")
                if not _passes(policy, value):
                    continue
                for offset in range(12):
                    ts = start + (index + offset) * 60
                    events.append(
                        _event(
                            policy,
                            str(service.get("display_name")),
                            "svc.service",
                            value,
                            ts,
                            offset,
                            "replay.deepflow",
                            {
                                "service": service.get("display_name"),
                                "role": service.get("role"),
                                "namespace": service.get("namespace"),
                            },
                        )
                    )
    return events


def _declared_events(
    snapshot: dict[str, Any],
    policies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_signal = {
        "slo_burn_rate": "slo-burn",
        "node_cpu_utilization": "node-cpu",
    }
    by_id = {str(item["id"]): item for item in policies}
    events: list[dict[str, Any]] = []
    for alert in snapshot.get("entities") or []:
        if alert.get("__entity_type__") != "ops.alert":
            continue
        policy_id = by_signal.get(str(alert.get("signal") or ""), "declared")
        policy = by_id.get(
            policy_id,
            {
                "id": policy_id,
                "name": "Declared alert",
                "metric": alert.get("signal") or "alert",
                "threshold": 0,
                "severity": alert.get("severity"),
            },
        )
        value = 0.0
        try:
            value = float(str(alert.get("value") or "0").rstrip("x%"))
        except ValueError:
            pass
        events.append(
            _event(
                policy,
                str(alert.get("target") or ""),
                str(alert.get("target_type") or "svc.service"),
                value,
                int(alert.get("fired_ts") or 0),
                99,
                "replay.alert",
                {"alert": alert.get("display_name")},
            )
        )
    return events


def _silenced(event: dict[str, Any], silences: list[dict[str, Any]], start: int) -> bool:
    minute = (int(event.get("fired_ts") or 0) - start) // 60
    for silence in silences:
        if event.get("target") != silence.get("target"):
            continue
        if int(silence.get("start_minute") or 0) <= minute < int(silence.get("end_minute") or 0):
            return True
    return False


def _group_key(
    event: dict[str, Any],
    policy: dict[str, Any],
    root: str,
) -> str:
    target = str(event.get("target") or "")
    group_by = [str(item) for item in (policy.get("group_by") or [])]
    if str(event.get("target_type")) == "infra.node" or "node" in group_by:
        return f"node:{target}"
    if "target" in group_by:
        return f"svc:{target}"
    if "root" in group_by and root:
        return f"svc:{root}"
    if policy.get("inhibit_downstream") and root:
        return f"svc:{root}"
    return f"svc:{target}"


def _group_rca(
    group: dict[str, Any],
    analysis: dict[str, Any],
    code_rca: dict[str, Any],
    policy_auto: bool,
) -> dict[str, Any]:
    root = str(group.get("root_service") or "")
    ranking = analysis.get("ranking") or []
    top = ranking[0] if ranking else {}
    code = code_rca.get("summary") or {}
    if not policy_auto:
        return {
            "auto": False,
            "mode": "node-correlation",
            "reason": "node-level alert; correlate pod placement before attributing a service root",
            "candidate": group.get("key", ""),
        }
    if root:
        return {
            "auto": True,
            "mode": "graph-rca",
            "root_cause": root,
            "confidence": analysis.get("confidence"),
            "reasons": list(top.get("reasons") or [])[:3],
            "ranking": ranking[:3],
            "code_fixes": int(code.get("fixed") or 0),
        }
    return {
        "auto": True,
        "mode": "graph-rca",
        "root_cause": top.get("service") or "",
        "confidence": top.get("confidence"),
        "reasons": list(top.get("reasons") or [])[:3],
    }


def compress(
    events: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    silences: list[dict[str, Any]],
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    code_rca: dict[str, Any] | None = None,
) -> dict[str, Any]:
    code_rca = code_rca or {}
    start = int((snapshot.get("window") or {}).get("start") or 0)
    root = str(analysis.get("root_cause") or "")
    policy_by_id = {str(item["id"]): item for item in policies}
    ordered = sorted(events, key=lambda item: (item.get("fired_ts") or 0, item.get("id") or ""))
    last: dict[str, int] = {}
    deduped = 0
    silenced_events = []
    kept: list[dict[str, Any]] = []
    for event in ordered:
        policy = policy_by_id.get(str(event.get("policy_id")), {})
        if _silenced(event, silences, start):
            silenced_events.append(event)
            continue
        fingerprint = "|".join(
            [
                str(event.get("policy_id")),
                str(event.get("target")),
                str(event.get("metric")),
            ]
        )
        window = int(policy.get("dedupe_window_seconds") or 300)
        previous = last.get(fingerprint)
        if previous is not None and int(event.get("fired_ts") or 0) - previous < window:
            deduped += 1
            continue
        last[fingerprint] = int(event.get("fired_ts") or 0)
        kept.append(event)

    groups: dict[str, dict[str, Any]] = {}
    for event in kept:
        policy = policy_by_id.get(str(event.get("policy_id")), {})
        key = _group_key(event, policy, root)
        group = groups.setdefault(
            key,
            {
                "key": key,
                "root_service": root if key.startswith("svc:") else "",
                "members": [],
                "policy_ids": set(),
                "first_seen": event.get("fired_at"),
                "last_seen": event.get("fired_at"),
                "max_severity": event.get("severity"),
            },
        )
        group["members"].append(event)
        group["policy_ids"].add(str(event.get("policy_id")))
        if SEVERITY_RANK.get(str(event.get("severity")), 9) < SEVERITY_RANK.get(
            str(group.get("max_severity")), 9
        ):
            group["max_severity"] = event.get("severity")
        group["last_seen"] = event.get("fired_at")

    output_groups = []
    for index, group in enumerate(groups.values(), start=1):
        members = group["members"]
        root_event = next(
            (item for item in members if item.get("target") == group.get("root_service")),
            None,
        )
        inhibited = 0
        for member in members:
            if root_event and member is not root_event and member.get("severity") in {"P2", "P3"}:
                member["inhibited_by"] = root_event.get("id")
                inhibited += 1
        policy_auto = any(
            bool(policy_by_id.get(str(pid), {}).get("auto_rca")) for pid in group["policy_ids"]
        )
        group_id = "GRP-" + hashlib.sha1(str(group["key"]).encode("utf-8")).hexdigest()[:8]
        item = {
            "id": group_id,
            "rank": index,
            "key": group["key"],
            "title": (
                f"{group['root_service']} 告警组"
                if group.get("root_service")
                else f"{group['key']} 告警组"
            ),
            "severity": group.get("max_severity"),
            "status": "firing",
            "root_service": group.get("root_service"),
            "policy_ids": sorted(group["policy_ids"]),
            "member_count": len(members),
            "inhibited_count": inhibited,
            "first_seen": group.get("first_seen"),
            "last_seen": group.get("last_seen"),
            "members": members,
        }
        item["rca"] = _group_rca(item, analysis, code_rca, policy_auto)
        output_groups.append(item)

    output_groups.sort(
        key=lambda item: (SEVERITY_RANK.get(str(item.get("severity")), 9), item.get("rank", 0))
    )
    for position, group in enumerate(output_groups, start=1):
        group["rank"] = position
    input_count = len(events)
    return {
        "stats": {
            "input_events": input_count,
            "after_dedupe": len(kept),
            "groups": len(output_groups),
            "deduped": deduped,
            "silenced": len(silenced_events),
            "inhibited": sum(item["inhibited_count"] for item in output_groups),
            "compression_ratio": round(1 - (len(output_groups) / input_count), 3)
            if input_count
            else 0.0,
        },
        "groups": output_groups,
        "silenced_events": silenced_events,
        "events": kept,
    }


def run_alerting(
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    policy_path: str | Path | None = None,
    code_rca: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = load_policies(policy_path)
    policies = list(config.get("policies") or [])
    silences = list(config.get("silences") or [])
    events = _generated_events(snapshot, policies) + _declared_events(snapshot, policies)
    result = compress(events, policies, silences, snapshot, analysis, code_rca)
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "source": "deepflow replay + alert policy engine",
        "policy_file": str(policy_path or DEFAULT_POLICY_PATH),
        "policies": policies,
        "silences": silences,
        "stats": result["stats"],
        "groups": result["groups"],
        "silenced_events": result["silenced_events"],
        "events": result["events"],
    }


def render_alert_markdown(result: dict[str, Any]) -> str:
    stats = result.get("stats") or {}
    lines = [
        "# 智能告警报告",
        "",
        f"- 输入事件：{stats.get('input_events', 0)}",
        f"- 去重后：{stats.get('after_dedupe', 0)}",
        f"- 告警组：{stats.get('groups', 0)}",
        f"- 去重：{stats.get('deduped', 0)} · 抑制：{stats.get('inhibited', 0)} · 静默：{stats.get('silenced', 0)}",
        f"- 压缩率：{float(stats.get('compression_ratio') or 0) * 100:.1f}%",
        "",
        "## 策略",
        "",
        "| 策略 | 指标 | 阈值 | 级别 | 自动 RCA |",
        "|---|---|---:|---|---|",
    ]
    for policy in result.get("policies") or []:
        lines.append(
            f"| {policy.get('name')} | `{policy.get('metric')}` | {policy.get('threshold')} | "
            f"{policy.get('severity')} | {'yes' if policy.get('auto_rca') else 'no'} |"
        )
    lines += [
        "",
        "## 告警组",
        "",
        "| # | 组 | 级别 | 成员 | 抑制 | 根因 |",
        "|---:|---|---|---:|---:|---|",
    ]
    for group in result.get("groups") or []:
        rca = group.get("rca") or {}
        lines.append(
            f"| {group.get('rank')} | {group.get('title')} | {group.get('severity')} | "
            f"{group.get('member_count')} | {group.get('inhibited_count')} | "
            f"`{rca.get('root_cause') or '-'}` |"
        )
    lines += ["", "## 静默", ""]
    for event in result.get("silenced_events") or []:
        lines.append(
            f"- `{event.get('target')}` {event.get('policy_name')} "
            f"({event.get('severity')}) at {event.get('fired_at')}"
        )
    return "\n".join(lines) + "\n"


def write_alert_artifacts(result: dict[str, Any], root: str | Path) -> dict[str, str]:
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    json_path = write_json(target / "alerts.json", result)
    md_path = target / "alerts.md"
    md_path.write_text(render_alert_markdown(result), encoding="utf-8")
    return {"json": str(json_path), "report": str(md_path)}
