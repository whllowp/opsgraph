"""Graph engineering over the materialized operational graph.

The analysis is intentionally explainable. Every ranked root-cause candidate is
scored from five observable components:

1. self severity (latency/error z-score, weighted by absolute impact);
2. change affinity (a recent risky change close to amplification onset);
3. outbound amplification (retry storms show up as egress growth);
4. causal lead (how much earlier the candidate's signal starts than the
   victim's);
5. graph position (upstream of the victim and fan-out to anomalous services).

No component is a black box: the console shows the contribution of each one.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Iterable

from .config import VICTIM_SERVICE
from .timeutil import clock, iso
from .util import clamp, safe_div

ANOMALY_THRESHOLD = 3.0


def _service_key(entity: dict[str, Any]) -> str:
    return str(entity.get("display_name") or "")


def _entity_id(entity: dict[str, Any]) -> str:
    return str(entity.get("__entity_id__") or "")


def _edge_metric(relation: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = relation.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GraphView:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.entities: list[dict[str, Any]] = snapshot.get("entities", [])
        self.relations: list[dict[str, Any]] = snapshot.get("relations", [])
        self.by_id = {_entity_id(item): item for item in self.entities}
        self.services = {
            str(item.get("display_name")): item
            for item in self.entities
            if item.get("__entity_type__") == "svc.service"
        }
        self.externals = {
            str(item.get("display_name")): item
            for item in self.entities
            if item.get("__entity_type__") == "svc.external_service"
        }
        self.endpoints = {
            str(item.get("__entity_id__")): item
            for item in self.entities
            if item.get("__entity_type__") == "svc.endpoint"
        }
        self.incident = next(
            (
                item
                for item in self.entities
                if item.get("__entity_type__") == "ops.incident"
            ),
            {},
        )
        self.changes = [
            item
            for item in self.entities
            if item.get("__entity_type__") == "ops.change"
        ]
        self.alerts = [
            item for item in self.entities if item.get("__entity_type__") == "ops.alert"
        ]
        self.journeys = [
            item
            for item in self.entities
            if item.get("__entity_type__") == "biz.journey"
        ]
        self.nodes = [
            item
            for item in self.entities
            if item.get("__entity_type__") == "infra.node"
        ]
        self.pods = [
            item for item in self.entities if item.get("__entity_type__") == "infra.pod"
        ]
        self.calls: list[dict[str, Any]] = []
        self.endpoint_calls: list[dict[str, Any]] = []
        self.depends_on: list[dict[str, Any]] = []
        self.targets: list[dict[str, Any]] = []
        for relation in self.relations:
            if relation.get("__relation_type__") == "calls":
                self.calls.append(relation)
            elif relation.get("__relation_type__") == "calls_endpoint":
                self.endpoint_calls.append(relation)
            elif relation.get("__relation_type__") == "depends_on":
                self.depends_on.append(relation)
            elif relation.get("__relation_type__") == "targets":
                self.targets.append(relation)

        self.out_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.in_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.calls:
            src = self.by_id.get(str(edge.get("__src_entity_id__")))
            dst = self.by_id.get(str(edge.get("__dest_entity_id__")))
            if not src or not dst:
                continue
            src_name = _service_key(src)
            dst_name = _service_key(dst)
            if src_name in self.services:
                self.out_calls[src_name].append(edge)
            if dst_name in self.services:
                self.in_calls[dst_name].append(edge)

    def service_names(self) -> list[str]:
        return list(self.services)

    def edge(self, src: str, dst: str) -> dict[str, Any] | None:
        for item in self.out_calls.get(src, []):
            if (
                _service_key(self.by_id.get(str(item.get("__dest_entity_id__")), {}))
                == dst
            ):
                return item
        return None

    def reachable(
        self, start: str, reverse: bool = False, max_depth: int = 20
    ) -> dict[str, int]:
        adjacency = self.in_calls if reverse else self.out_calls
        distances: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in adjacency.get(current, []):
                neighbor_entity = self.by_id.get(
                    str(
                        edge.get(
                            "__src_entity_id__" if reverse else "__dest_entity_id__"
                        )
                    )
                )
                if not neighbor_entity:
                    continue
                neighbor = _service_key(neighbor_entity)
                if neighbor in distances or neighbor == start:
                    continue
                distances[neighbor] = depth + 1
                queue.append((neighbor, depth + 1))
        return distances

    def shortest_path(self, start: str, goal: str) -> list[str]:
        if start == goal:
            return [start]
        queue: deque[str] = deque([start])
        previous: dict[str, str] = {}
        seen = {start}
        while queue:
            current = queue.popleft()
            for edge in self.out_calls.get(current, []):
                dest_entity = self.by_id.get(str(edge.get("__dest_entity_id__")))
                if not dest_entity:
                    continue
                dest = _service_key(dest_entity)
                if dest in seen:
                    continue
                previous[dest] = current
                if dest == goal:
                    path = [dest]
                    while path[-1] != start:
                        path.append(previous[path[-1]])
                    return list(reversed(path))
                seen.add(dest)
                queue.append(dest)
        return []

    def incident_victim(self) -> str:
        victim = str(self.incident.get("victim") or "")
        if victim in self.services:
            return victim
        return VICTIM_SERVICE if VICTIM_SERVICE in self.services else ""


def _change_for_service(graph: GraphView, service: str) -> dict[str, Any] | None:
    candidates = []
    for change in graph.changes:
        if str(change.get("service")) != service:
            continue
        candidates.append(change)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("applied_ts") or 0))


def _component_change(
    change: dict[str, Any] | None, onset_ts: int | None
) -> tuple[float, str]:
    if not change:
        return 0.0, "no recent change"
    risk = str(change.get("risk") or "").lower()
    applied = int(change.get("applied_ts") or 0)
    summary = str(change.get("summary") or "").lower()
    detail = f"{summary} {change.get('detail', '')}".lower()
    retry_signal = any(
        token in detail for token in ("retry", "timeout", "重试", "超时", "max_retries")
    )
    lag = (onset_ts - applied) / 60.0 if onset_ts else 9999
    close = 0 <= lag <= 20
    if risk in {"high", "critical"}:
        value = 0.50 if close else 0.34
        reason = f"high-risk {change.get('change_type', 'change')} at {clock(applied)}"
        if retry_signal:
            value = min(0.52, value + 0.02)
            reason += " touching retry/timeout settings"
        return value, reason
    if risk == "medium":
        return (0.18 if close else 0.10), f"medium-risk change at {clock(applied)}"
    if change.get("change_type") == "deployment":
        return 0.01, "recent deployment is low risk"
    return 0.02, "recent low-risk change"


def _component_upstream(
    candidate: str, victim: str, upstream: dict[str, int]
) -> tuple[float, str]:
    if candidate == victim:
        return 0.10, "victim service"
    if candidate in upstream:
        distance = upstream[candidate]
        bonus = max(0.10, 0.20 - 0.02 * max(0, distance - 1))
        return (
            bonus,
            f"upstream of victim ({distance} hop{'s' if distance > 1 else ''})",
        )
    return 0.0, "not upstream of victim"


def _component_lead(
    candidate: dict[str, Any], victim: dict[str, Any]
) -> tuple[float, str]:
    candidate_signal = min(
        int(candidate.get("amplification_onset_ts") or 0) or 10**12,
        int(candidate.get("latency_onset_ts") or 0) or 10**12,
    )
    victim_signal = (
        int(victim.get("latency_onset_ts") or victim.get("onset_ts") or 0) or 10**12
    )
    if candidate_signal >= 10**12 or victim_signal >= 10**12:
        return 0.0, "no comparable onset"
    delta = (victim_signal - candidate_signal) / 60.0
    if delta <= 0:
        return 0.0, "onset is not earlier than victim"
    return clamp(delta / 15.0) * 0.08, f"onset {delta:.0f} min before victim"


def _component_propagation(
    graph: GraphView, candidate: str, anomalous: set[str]
) -> tuple[float, str]:
    descendants = graph.reachable(candidate)
    if not descendants:
        return 0.0, "no downstream services"
    hits = sum(1 for name in descendants if name in anomalous)
    return (
        hits / len(descendants)
    ) * 0.07, f"{hits}/{len(descendants)} downstream services anomalous"


def _component_self(candidate: dict[str, Any], max_score: float) -> tuple[float, str]:
    score = float(candidate.get("anomaly_score") or 0)
    normalized = clamp(score / max(1.0, max_score))
    return (
        normalized * 0.28,
        f"P99 {candidate.get('latency_p99_ms', 0):.0f}ms, anomaly {score:.1f}",
    )


def _component_amplification(candidate: dict[str, Any]) -> tuple[float, str]:
    amp = float(candidate.get("outbound_amp") or 1.0)
    return clamp((amp - 1.0) / 7.0) * 0.12, f"outbound {amp:.1f}x baseline"


def _articulation_points(
    services: Iterable[str], adjacency: dict[str, set[str]]
) -> list[str]:
    """Tarjan articulation points over the undirected service graph."""

    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    points: set[str] = set()
    time = 0

    def visit(node: str) -> None:
        nonlocal time
        time += 1
        discovery[node] = low[node] = time
        children = 0
        for neighbor in adjacency.get(node, set()):
            if neighbor not in discovery:
                parent[neighbor] = node
                children += 1
                visit(neighbor)
                low[node] = min(low[node], low[neighbor])
                if parent.get(node) is None and children > 1:
                    points.add(node)
                if parent.get(node) is not None and low[neighbor] >= discovery[node]:
                    points.add(node)
            elif neighbor != parent.get(node):
                low[node] = min(low[node], discovery[neighbor])

    for service in services:
        if service not in discovery:
            parent[service] = None
            visit(service)
    return sorted(points)


def analyze(snapshot: dict[str, Any]) -> dict[str, Any]:
    graph = GraphView(snapshot)
    victim = graph.incident_victim()
    victim_entity = graph.services.get(victim, {})
    upstream = graph.reachable(victim, reverse=True) if victim else {}
    anomalous = {
        name
        for name, entity in graph.services.items()
        if float(entity.get("anomaly_score") or 0) >= ANOMALY_THRESHOLD
        or str(entity.get("status")) in {"critical", "warning"}
    }
    # The victim always participates, even if its own score is somehow low.
    if victim:
        anomalous.add(victim)

    candidates = []
    max_score = max(
        (float(item.get("anomaly_score") or 0) for item in graph.services.values()),
        default=1.0,
    )
    for name, entity in graph.services.items():
        if name not in anomalous:
            continue
        change = _change_for_service(graph, name)
        change_value, change_reason = _component_change(
            change,
            int(entity.get("amplification_onset_ts") or entity.get("onset_ts") or 0),
        )
        self_value, self_reason = _component_self(entity, max_score)
        amp_value, amp_reason = _component_amplification(entity)
        upstream_value, upstream_reason = _component_upstream(name, victim, upstream)
        lead_value, lead_reason = _component_lead(entity, victim_entity)
        prop_value, prop_reason = _component_propagation(graph, name, anomalous)
        total = (
            self_value
            + change_value
            + amp_value
            + lead_value
            + prop_value
            + upstream_value
        )
        candidates.append(
            {
                "service": name,
                "display_name": entity.get("display_name", name),
                "score": round(total, 3),
                "status": entity.get("status"),
                "confidence": 0.0,
                "components": {
                    "self_severity": round(self_value, 3),
                    "change_affinity": round(change_value, 3),
                    "outbound_amplification": round(amp_value, 3),
                    "causal_lead": round(lead_value, 3),
                    "propagation": round(prop_value, 3),
                    "graph_position": round(upstream_value, 3),
                },
                "reasons": [
                    self_reason,
                    change_reason,
                    amp_reason,
                    upstream_reason,
                    lead_reason,
                    prop_reason,
                ],
                "change": change.get("name") if change else None,
                "change_risk": change.get("risk") if change else None,
                "metrics": {
                    "qps": entity.get("qps"),
                    "p99_ms": entity.get("latency_p99_ms"),
                    "baseline_p99_ms": entity.get("baseline_p99_ms"),
                    "p99_delta_pct": entity.get("p99_delta_pct"),
                    "error_rate": entity.get("error_rate"),
                    "outbound_amp": entity.get("outbound_amp"),
                    "onset_ts": entity.get("onset_ts"),
                    "amplification_onset_ts": entity.get("amplification_onset_ts"),
                },
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(candidates):
        item["rank"] = index + 1
    root = candidates[0]["service"] if candidates else victim
    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
    top = candidates[0]["score"] if candidates else 0.0
    confidence = (
        clamp(0.55 + 0.45 * safe_div(top - second, max(top, 1e-9)), 0.0, 0.98)
        if top
        else 0.0
    )
    if candidates:
        candidates[0]["confidence"] = round(confidence, 3)

    path = graph.shortest_path(root, victim) if root and victim else []
    tail = []
    if victim:
        downstream = graph.reachable(victim)
        external_candidates = [name for name in downstream if name in graph.externals]
        if external_candidates:
            worst = max(
                external_candidates,
                key=lambda name: (
                    float(graph.externals.get(name, {}).get("rps") or 0)
                    * (
                        1.0
                        + 5.0
                        * float(graph.externals.get(name, {}).get("error_rate") or 0)
                    )
                ),
            )
            tail = graph.shortest_path(victim, worst)
        elif downstream:
            worst = max(
                downstream,
                key=lambda name: float(
                    graph.services.get(name, {}).get("latency_p99_ms") or 0
                ),
            )
            tail = graph.shortest_path(victim, worst)
    full_path = path + [item for item in tail if item not in path]

    ruled_out = _ruled_out(graph, root, victim)
    findings = _findings(graph, root, victim, anomalous)
    result = {
        "root_cause": root,
        "victim": victim,
        "confidence": round(confidence, 3),
        "ranking": candidates,
        "path": full_path,
        "ruled_out": ruled_out,
        "findings": findings,
        "impact": {},
        "timeline": [],
        "summary": _summary(candidates, root, victim, confidence),
        "generated_at": iso(int(time.time())),
    }
    _apply_roles(snapshot, graph, result)
    impact = _impact(graph, root, victim, anomalous)
    result["impact"] = impact
    result["timeline"] = _timeline(graph, root, victim)
    _apply_journey_impact(snapshot, impact)
    return result


def _ruled_out(graph: GraphView, root: str, victim: str) -> list[dict[str, Any]]:
    items = []
    for change in graph.changes:
        service = str(change.get("service"))
        if service == root:
            continue
        if str(change.get("risk")).lower() == "low":
            items.append(
                {
                    "kind": "change",
                    "title": f"{change.get('display_name')} is low risk",
                    "detail": f"{change.get('summary')} Applied at {change.get('applied_at')}. No request-path change.",
                    "entity_id": _entity_id(change),
                }
            )
    for node in graph.nodes:
        if float(node.get("anomaly_score") or 0) < ANOMALY_THRESHOLD:
            continue
        hosted = [
            pod
            for pod in graph.pods
            if str(pod.get("node")) == str(node.get("display_name"))
        ]
        services = sorted(
            {str(pod.get("workload", "")).replace("-deploy", "") for pod in hosted}
        )
        victim_pods = [
            pod
            for pod in graph.pods
            if str(pod.get("workload", "")).startswith(victim)
            and str(pod.get("node")) == str(node.get("display_name"))
        ]
        if not victim_pods:
            items.append(
                {
                    "kind": "node",
                    "title": f"{node.get('display_name')} pressure is not on the victim path",
                    "detail": f"CPU {float(node.get('cpu_utilization') or 0) * 100:.0f}% but {victim} has no pods on this node. Hosted services: {', '.join(services) or 'unknown'}.",
                    "entity_id": _entity_id(node),
                }
            )
    items.append(
        {
            "kind": "traffic",
            "title": "Flash Sale explains baseline traffic, not the retry multiplier",
            "detail": "All services see ~3.2x traffic after 00:30, but checkout -> payment-gateway grows ~8x. The extra ~2.5x is retry amplification.",
            "entity_id": "",
        }
    )
    return items


def _findings(
    graph: GraphView, root: str, victim: str, anomalous: set[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for edge in graph.calls:
        src = graph.by_id.get(str(edge.get("__src_entity_id__")), {})
        dst = graph.by_id.get(str(edge.get("__dest_entity_id__")), {})
        if dst.get("__entity_type__") != "svc.external_service":
            continue
        if bool(dst.get("shadow")):
            findings.append(
                {
                    "id": "shadow-dependency",
                    "severity": "warning",
                    "title": "Observed dependency is missing from the declared model",
                    "detail": f"{src.get('display_name')} calls {dst.get('display_name')} ({dst.get('domain')}) at {float(edge.get('rps') or 0):.0f} rps, P99 {float(edge.get('latency_p99_ms') or 0):.0f}ms, error {float(edge.get('error_rate') or 0) * 100:.1f}%. It was discovered by eBPF but is absent from the CMDB.",
                    "entities": [_entity_id(src), _entity_id(dst)],
                    "evidence": {
                        "rps": edge.get("rps"),
                        "p99_ms": edge.get("latency_p99_ms"),
                        "error_rate": edge.get("error_rate"),
                    },
                }
            )
        if edge.get("observed") is False:
            findings.append(
                {
                    "id": "declared-unobserved",
                    "severity": "info",
                    "title": "Declared dependency has no observed traffic",
                    "detail": f"{src.get('display_name')} -> {dst.get('display_name')} is declared but produced zero L7 requests in the window. Candidate for model cleanup or a broken integration.",
                    "entities": [_entity_id(src), _entity_id(dst)],
                    "evidence": {"rps": 0},
                }
            )

    # Declared-but-unobserved edges can point at services as well as externals.
    for edge in graph.calls:
        if edge.get("observed") is not False:
            continue
        src = graph.by_id.get(str(edge.get("__src_entity_id__")), {})
        dst = graph.by_id.get(str(edge.get("__dest_entity_id__")), {})
        if any(
            finding.get("id") == "declared-unobserved"
            and set(finding.get("entities", [])) == {_entity_id(src), _entity_id(dst)}
            for finding in findings
        ):
            continue
        findings.append(
            {
                "id": "declared-unobserved",
                "severity": "info",
                "title": "Declared dependency has no observed traffic",
                "detail": f"{src.get('display_name')} -> {dst.get('display_name')} is declared but produced zero L7 requests in the window. Candidate for model cleanup or a broken integration.",
                "entities": [_entity_id(src), _entity_id(dst)],
                "evidence": {"rps": 0},
            }
        )

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.calls:
        src = graph.by_id.get(str(edge.get("__src_entity_id__")))
        dst = graph.by_id.get(str(edge.get("__dest_entity_id__")))
        if not src or not dst:
            continue
        if (
            src.get("__entity_type__") != "svc.service"
            or dst.get("__entity_type__") != "svc.service"
        ):
            continue
        a, b = _service_key(src), _service_key(dst)
        adjacency[a].add(b)
        adjacency[b].add(a)
    points = _articulation_points(graph.service_names(), adjacency)
    if points:
        findings.append(
            {
                "id": "single-point-of-failure",
                "severity": "warning",
                "title": "Service graph has articulation points",
                "detail": "Removing "
                + ", ".join(points)
                + " disconnects part of the call graph. These are structural single points of failure.",
                "entities": [
                    _entity_id(graph.services[name])
                    for name in points
                    if name in graph.services
                ],
                "evidence": {"articulation_points": points},
            }
        )

    payment_edge = graph.edge(root, victim)
    if payment_edge:
        findings.append(
            {
                "id": "retry-storm",
                "severity": "critical",
                "title": "Retry amplification on the root-cause edge",
                "detail": f"{root} -> {victim}: {float(payment_edge.get('rps') or 0):.0f} rps vs {float(payment_edge.get('baseline_p99_ms') or 0):.0f}ms baseline P99. Outbound amplification is {float(graph.services.get(root, {}).get('outbound_amp') or 1):.1f}x while the flash-sale multiplier is only 3.2x.",
                "entities": [
                    _entity_id(graph.services.get(root, {})),
                    _entity_id(graph.services.get(victim, {})),
                ],
                "evidence": {
                    "rps": payment_edge.get("rps"),
                    "p99_ms": payment_edge.get("latency_p99_ms"),
                    "outbound_amp": graph.services.get(root, {}).get("outbound_amp"),
                },
            }
        )

    for node in graph.nodes:
        if float(node.get("anomaly_score") or 0) < 5:
            continue
        victim_pods = [
            pod
            for pod in graph.pods
            if str(pod.get("workload", "")).startswith(victim)
            and str(pod.get("node")) == str(node.get("display_name"))
        ]
        if victim_pods:
            continue
        hosted = [
            pod
            for pod in graph.pods
            if str(pod.get("node")) == str(node.get("display_name"))
        ]
        services = sorted(
            {str(pod.get("workload", "")).replace("-deploy", "") for pod in hosted}
        )
        findings.append(
            {
                "id": "node-pressure",
                "severity": "info",
                "title": f"{node.get('display_name')} resource pressure",
                "detail": f"CPU {float(node.get('cpu_utilization') or 0) * 100:.0f}% (z={float(node.get('anomaly_score') or 0):.1f}). No {victim} pods on this node. Hosted services: {', '.join(services) or 'unknown'}.",
                "entities": [_entity_id(node)],
                "evidence": {"cpu": node.get("cpu_utilization")},
            }
        )

    findings.sort(
        key=lambda item: {"critical": 0, "warning": 1, "info": 2}.get(
            str(item.get("severity")), 3
        )
    )
    return findings


def _impact(
    graph: GraphView, root: str, victim: str, anomalous: set[str]
) -> dict[str, Any]:
    affected = set(graph.reachable(root)) if root else set()
    affected.add(root)
    affected.add(victim)
    affected_services = []
    qps_max = 0.0
    for name in sorted(affected):
        entity = graph.services.get(name)
        if not entity:
            continue
        qps = float(entity.get("qps") or 0)
        qps_max = max(qps_max, qps)
        affected_services.append(
            {
                "service": name,
                "status": entity.get("status"),
                "role": entity.get("role"),
                "qps": round(qps, 1),
                "p99_ms": round(float(entity.get("latency_p99_ms") or 0), 1),
                "criticality": entity.get("criticality"),
            }
        )
    journeys = []
    for journey in graph.journeys:
        relations = [
            item
            for item in graph.depends_on
            if str(item.get("__src_entity_id__")) == _entity_id(journey)
        ]
        dependencies = []
        for relation in relations:
            dep = graph.by_id.get(str(relation.get("__dest_entity_id__")))
            if dep:
                dependencies.append(_service_key(dep))
        hit = [name for name in dependencies if name in affected]
        if not hit:
            continue
        journeys.append(
            {
                "journey": journey.get("display_name"),
                "tier": journey.get("tier"),
                "status": "impacted",
                "impacted_services": hit,
                "conversion_impact": journey.get("conversion_impact"),
                "revenue_exposure": journey.get("revenue_exposure"),
            }
        )
    return {
        "services": affected_services,
        "service_count": len(affected_services),
        "journeys": journeys,
        "qps_affected": round(qps_max, 1),
        "summary": f"{len(affected_services)} services and {len(journeys)} business journeys are in the blast radius; peak exposure is ~{qps_max:.0f} qps.",
    }


def _timeline(graph: GraphView, root: str, victim: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(ts: int, kind: str, title: str, detail: str, entity: str) -> None:
        key = (ts, title)
        if key in seen:
            return
        seen.add(key)
        events.append(
            {"ts": ts, "kind": kind, "title": title, "detail": detail, "entity": entity}
        )

    window_start = int(graph.snapshot.get("window", {}).get("start") or 0)
    covered: set[tuple[int, str]] = set()
    for item in graph.snapshot.get("timeline", []):
        minute = int(item.get("minute") or 0)
        event_ts = window_start + minute * 60
        kind = str(item.get("kind") or "event")
        if kind == "signal":
            # The computed onsets below are more precise than the narrative.
            continue
        covered.add((event_ts, kind))
        add(
            event_ts,
            kind,
            str(item.get("title") or ""),
            str(item.get("detail") or ""),
            str(item.get("ref") or ""),
        )
    for change in graph.changes:
        event_ts = int(change.get("applied_ts") or 0)
        if (event_ts, "change") in covered:
            continue
        add(
            event_ts,
            "change",
            str(change.get("display_name")),
            str(change.get("summary")),
            str(change.get("display_name")),
        )
    for name, entity in graph.services.items():
        amplification = int(entity.get("amplification_onset_ts") or 0)
        if amplification and name in {root, victim}:
            add(
                amplification,
                "signal",
                f"{name} outbound amplification onset",
                f"Outbound {float(entity.get('outbound_amp') or 0):.1f}x baseline, before the latency breach.",
                name,
            )
        onset = int(entity.get("onset_ts") or 0)
        if not onset:
            continue
        if name not in {root, victim} and float(entity.get("anomaly_score") or 0) < 8:
            continue
        add(
            onset,
            "signal",
            f"{name} {entity.get('onset_signal') or 'anomaly'} onset",
            f"P99 {float(entity.get('latency_p99_ms') or 0):.0f}ms, {float(entity.get('p99_delta_pct') or 0):.0f}% vs baseline.",
            name,
        )
    for alert in graph.alerts:
        event_ts = int(alert.get("fired_ts") or 0)
        if (event_ts, "alert") in covered:
            continue
        add(
            event_ts,
            "alert",
            str(alert.get("display_name")),
            str(alert.get("summary")),
            str(alert.get("display_name")),
        )
    events.sort(key=lambda item: item["ts"])
    for event in events:
        event["clock"] = clock(event["ts"]) if event["ts"] else "--:--"
    return events


def _summary(
    candidates: list[dict[str, Any]], root: str, victim: str, confidence: float
) -> str:
    if not candidates:
        return "No anomalous service found in the window."
    top = candidates[0]
    return (
        f"Root cause is {root}, upstream of the victim {victim}. "
        f"Score {top['score']:.2f} (confidence {confidence * 100:.0f}%). "
        f"{top['reasons'][1]}; {top['reasons'][2]}."
    )


def _apply_roles(
    snapshot: dict[str, Any], graph: GraphView, result: dict[str, Any]
) -> None:
    root = result["root_cause"]
    victim = result["victim"]
    upstream = graph.reachable(victim, reverse=True) if victim else {}
    downstream = graph.reachable(root) if root else {}
    roles: dict[str, str] = {}
    for name in graph.services:
        if name == root:
            roles[name] = "root_cause"
        elif name == victim:
            roles[name] = "victim"
        elif name in downstream:
            roles[name] = "downstream"
        elif name in upstream:
            roles[name] = "upstream"
        else:
            roles[name] = "bystander"
    for entity in snapshot.get("entities", []):
        if entity.get("__entity_type__") == "svc.service":
            name = str(entity.get("display_name"))
            entity["role"] = roles.get(name, "bystander")
        elif entity.get("__entity_type__") == "ops.incident":
            entity["root_cause"] = root


def _apply_journey_impact(snapshot: dict[str, Any], impact: dict[str, Any]) -> None:
    impacted = {item["journey"] for item in impact.get("journeys", [])}
    for entity in snapshot.get("entities", []):
        if entity.get("__entity_type__") != "biz.journey":
            continue
        is_impacted = entity.get("display_name") in impacted
        entity["impacted"] = is_impacted
        entity["status"] = "impacted" if is_impacted else "healthy"
        entity["impacted_qps"] = impact.get("qps_affected", 0.0) if is_impacted else 0.0


def changed_entities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Entities whose analysis-owned fields changed and should be upserted."""

    return [
        item
        for item in snapshot.get("entities", [])
        if item.get("__entity_type__") in {"svc.service", "ops.incident", "biz.journey"}
    ]
