"""Turn DeepFlow observations + declared topology into a UModel graph.

The materializer is deliberately deterministic:

* every edge carries the evidence that produced it (RPS, error rate, P99,
  baseline, onset, observation source);
* declared-but-unobserved edges are kept with ``observed=false`` instead of
  being silently dropped;
* observed-but-undeclared edges materialize their external endpoint and are
  marked ``shadow=true``.

The output is a pair of CMS 2.0 entity/relation batches plus a snapshot used
by the analysis engine and console.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from . import ids
from .config import SCENARIO_START, WINDOW_MINUTES, WORKSPACE
from .deepflow import FlowRecord, NodeMetric
from .timeutil import iso
from .util import median, robust_z, safe_div

KEEP_ALIVE = 600
BASELINE_MINUTES = 10
ONSET_Z = 3.5
ONSET_CONSECUTIVE = 2


@dataclass
class MinuteMetric:
    minute: int
    rps: float
    p99: float
    err: float
    requests: int
    bytes: int


def _entity(
    type_name: str,
    eid: str,
    display: str,
    fields: dict[str, Any],
    first: int,
    last: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "__domain__": type_name.split(".", 1)[0],
        "__entity_type__": type_name,
        "__entity_id__": eid,
        "__category__": "entity",
        "__method__": "Update",
        "__first_observed_time__": int(first),
        "__last_observed_time__": int(last),
        "__keep_alive_seconds__": KEEP_ALIVE,
        "id": eid,
        "display_name": display,
    }
    payload.update(fields)
    return payload


def _relation(
    src_type: str,
    src_id: str,
    dest_type: str,
    dest_id: str,
    relation_type: str,
    display: str,
    first: int,
    last: int,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "__src_domain__": src_type.split(".", 1)[0],
        "__src_entity_type__": src_type,
        "__src_entity_id__": src_id,
        "__dest_domain__": dest_type.split(".", 1)[0],
        "__dest_entity_type__": dest_type,
        "__dest_entity_id__": dest_id,
        "__relation_type__": relation_type,
        "__category__": "entity_link",
        "__method__": "Update",
        "__first_observed_time__": int(first),
        "__last_observed_time__": int(last),
        "__keep_alive_seconds__": KEEP_ALIVE,
        "display_name": display,
    }
    if fields:
        payload.update(fields)
    return payload


def _aggregate(records: list[FlowRecord], window_start: int) -> list[MinuteMetric]:
    buckets: dict[int, list[FlowRecord]] = defaultdict(list)
    for record in records:
        buckets[int((record.time - window_start) // 60)].append(record)
    result = []
    for minute in sorted(buckets):
        items = buckets[minute]
        requests = sum(item.requests for item in items)
        if requests <= 0:
            continue
        p99 = sum(item.p99_ms * item.requests for item in items) / requests
        err = (
            sum((item.client_errors + item.server_errors) for item in items) / requests
        )
        result.append(
            MinuteMetric(
                minute=minute,
                rps=requests / 60.0,
                p99=p99,
                err=err,
                requests=requests,
                bytes=sum(item.byte_tx + item.byte_rx for item in items),
            )
        )
    return result


def _baseline(series: list[MinuteMetric], field: str) -> list[float]:
    values = [
        float(getattr(item, field)) for item in series if item.minute < BASELINE_MINUTES
    ]
    return values or [float(getattr(series[0], field))] if series else []


def detect_onset(series: list[MinuteMetric], field: str) -> tuple[int | None, float]:
    """Return (onset minute, peak z-score) for one signal."""

    baseline = _baseline(series, field)
    if not baseline:
        return None, 0.0
    peak = 0.0
    run = 0
    for item in series:
        if item.minute < BASELINE_MINUTES:
            continue
        z = robust_z(float(getattr(item, field)), baseline, floor_ratio=0.04)
        peak = max(peak, z)
        if z >= ONSET_Z:
            run += 1
            if run >= ONSET_CONSECUTIVE:
                return item.minute - ONSET_CONSECUTIVE + 1, peak
        else:
            run = 0
    return None, peak


def _merge_series(series_list: Iterable[list[MinuteMetric]]) -> list[MinuteMetric]:
    buckets: dict[int, list[MinuteMetric]] = defaultdict(list)
    for series in series_list:
        for item in series:
            buckets[item.minute].append(item)
    merged = []
    for minute in sorted(buckets):
        items = buckets[minute]
        requests = sum(item.requests for item in items)
        if not requests:
            continue
        merged.append(
            MinuteMetric(
                minute=minute,
                rps=requests / 60.0,
                p99=sum(item.p99 * item.requests for item in items) / requests,
                err=sum(item.err * item.requests for item in items) / requests,
                requests=requests,
                bytes=sum(item.bytes for item in items),
            )
        )
    return merged


def _signal_stats(series: list[MinuteMetric]) -> dict[str, Any]:
    if not series:
        return {
            "onset_p99": None,
            "onset_err": None,
            "onset_rps": None,
            "z_p99": 0.0,
            "z_err": 0.0,
            "z_rps": 0.0,
        }
    onset_p99, z_p99 = detect_onset(series, "p99")
    onset_err, z_err = detect_onset(series, "err")
    onset_rps, z_rps = detect_onset(series, "rps")
    return {
        "onset_p99": onset_p99,
        "onset_err": onset_err,
        "onset_rps": onset_rps,
        "z_p99": z_p99,
        "z_err": z_err,
        "z_rps": z_rps,
    }


def _metric_summary(
    series: list[MinuteMetric],
    minute: int | None = None,
    window_start: int = SCENARIO_START,
) -> dict[str, Any]:
    if not series:
        return {
            "rps": 0.0,
            "p99": 0.0,
            "err": 0.0,
            "baseline_p99": 0.0,
            "p99_delta_pct": 0.0,
            "requests": 0,
            "bytes": 0,
            "onset_ts": None,
            "onset_minute": None,
            "onset_signal": None,
            "rps_onset_ts": None,
            "error_onset_ts": None,
            "anomaly_score": 0.0,
            "observed": False,
        }
    selected = (
        series[-1]
        if minute is None
        else min(series, key=lambda item: abs(item.minute - minute))
    )
    baseline_p99 = (
        median(_baseline(series, "p99")) if _baseline(series, "p99") else selected.p99
    )
    stats = _signal_stats(series)
    # z-scores alone make a 12ms -> 70ms change look worse than a 1.1s -> 2.2s
    # change. Weight the score by absolute severity so graph ranking follows
    # user-visible impact, while onset still uses the raw signal.
    p99_weight = max(0.03, min(1.0, selected.p99 / 1000.0))
    err_weight = max(0.03, min(1.0, selected.err / 0.05))
    severity_score = max(stats["z_p99"] * p99_weight, stats["z_err"] * err_weight * 1.5)
    # Onset describes *symptom* onset (latency or errors). A pure RPS rise is
    # reported separately as amplification onset; otherwise every service on a
    # traffic spike would look like a root cause.
    symptom_onsets = [
        value for value in (stats["onset_p99"], stats["onset_err"]) if value is not None
    ]
    onset = min(symptom_onsets) if symptom_onsets else None
    signal = None
    if onset is not None:
        if stats["onset_p99"] == onset:
            signal = "p99"
        elif stats["onset_err"] == onset:
            signal = "error"
    return {
        "rps": round(selected.rps, 3),
        "p99": round(selected.p99, 2),
        "err": round(selected.err, 5),
        "baseline_p99": round(baseline_p99, 2),
        "p99_delta_pct": round(
            safe_div(selected.p99 - baseline_p99, baseline_p99) * 100.0, 1
        ),
        "requests": selected.requests,
        "bytes": selected.bytes,
        "onset_ts": (window_start + onset * 60) if onset is not None else None,
        "onset_minute": onset,
        "onset_signal": signal,
        "rps_onset_ts": (window_start + stats["onset_rps"] * 60)
        if stats["onset_rps"] is not None
        else None,
        "error_onset_ts": (window_start + stats["onset_err"] * 60)
        if stats["onset_err"] is not None
        else None,
        "anomaly_score": round(severity_score, 2),
        "observed": True,
    }


class Materializer:
    def __init__(
        self,
        flows: list[FlowRecord],
        node_metrics: list[NodeMetric],
        declared: dict[str, Any] | None,
        window_start: int | None = None,
    ) -> None:
        self.flows = flows
        self.node_metrics = node_metrics
        self.declared = declared or {
            "services": [],
            "externals": [],
            "edges": [],
            "nodes": [],
            "pods": [],
            "workloads": [],
            "namespaces": [],
            "journeys": [],
            "changes": [],
            "alerts": [],
            "incident": {},
        }
        self.window_start = window_start or (
            min((item.time for item in flows), default=SCENARIO_START)
        )
        self.window_end = self.window_start + WINDOW_MINUTES * 60
        self.entities: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.snapshot_metrics: dict[str, Any] = {}
        self._service_meta: dict[str, dict[str, Any]] = {}
        self._external_meta: dict[str, dict[str, Any]] = {}
        self._endpoint_meta: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._edge_series: dict[tuple[str, str], list[MinuteMetric]] = {}
        self._endpoint_series: dict[tuple[str, str, str, str], list[MinuteMetric]] = {}
        self._service_series: dict[str, list[MinuteMetric]] = {}
        self._node_series: dict[str, list[NodeMetric]] = {}

    # --------------------------------------------------------------- indexing
    def _index(self) -> None:
        for item in self.declared.get("services", []):
            self._service_meta[item["name"]] = item
        for item in self.declared.get("externals", []):
            self._external_meta[item["name"]] = item
        workload_by_service = {
            item.get("service"): item["name"]
            for item in self.declared.get("workloads", [])
        }
        for name, meta in self._service_meta.items():
            if workload_by_service.get(name):
                meta["workload"] = workload_by_service[name]
        for service in self.declared.get("services", []):
            for method, path in service.get("endpoints", []):
                self._endpoint_meta[(service["name"], method, path)] = {
                    "service": service["name"],
                    "method": method,
                    "path": path,
                    "protocol": "HTTP",
                }
        for item in self.declared.get("externals", []):
            self._external_meta.setdefault(item["name"], item)

        by_pair: dict[tuple[str, str], list[FlowRecord]] = defaultdict(list)
        by_endpoint: dict[tuple[str, str, str, str], list[FlowRecord]] = defaultdict(
            list
        )
        for record in self.flows:
            server = self._resolve_server(record)
            pair = (record.client_service, server)
            by_pair[pair].append(record)
            if not record.external:
                by_endpoint[
                    (record.client_service, server, record.method, record.endpoint)
                ].append(record)
                self._endpoint_meta.setdefault(
                    (server, record.method, record.endpoint),
                    {
                        "service": server,
                        "method": record.method,
                        "path": record.endpoint,
                        "protocol": record.protocol,
                    },
                )
            self._service_meta.setdefault(
                record.client_service,
                {
                    "name": record.client_service,
                    "display": record.client_service,
                    "namespace": record.client_namespace or "observed",
                    "tier": "observed",
                    "language": "unknown",
                    "owner": "unknown",
                    "criticality": "silver",
                    "version": "observed",
                    "replicas": 0,
                    "nodes": [],
                    "endpoints": [],
                    "observed_only": True,
                },
            )
        for pair, records in by_pair.items():
            self._edge_series[pair] = _aggregate(records, self.window_start)
        for key, records in by_endpoint.items():
            self._endpoint_series[key] = _aggregate(records, self.window_start)

        for node in self.declared.get("nodes", []):
            series = [item for item in self.node_metrics if item.node == node["name"]]
            self._node_series[node["name"]] = sorted(series, key=lambda item: item.time)

        # Service-level series: inbound latency/errors. Outbound series are
        # merged on demand because amplification is an edge property.
        for service_name in self._service_meta:
            inbound = [
                records
                for (src, dst), records in self._edge_series.items()
                if dst == service_name
            ]
            self._service_series[service_name] = _merge_series(inbound)

    def _resolve_server(self, record: FlowRecord) -> str:
        if not record.external:
            return record.server_service
        domain = record.request_domain
        for item in self._external_meta.values():
            if item.get("domain") == domain:
                return item["name"]
        # Unknown dependency: keep the full domain as the stable key and make
        # it visible as an observed-only external entity.
        key = domain or record.server_service
        self._external_meta.setdefault(
            key,
            {
                "name": key,
                "display": key,
                "domain": domain,
                "kind": "observed_dependency",
                "provider": "unknown",
                "criticality": "silver",
                "declared": False,
                "shadow": True,
            },
        )
        return key

    # ------------------------------------------------------------------- build
    def build(self) -> dict[str, Any]:
        self._index()
        self._build_infra()
        self._build_services()
        self._build_endpoints()
        self._build_externals()
        self._build_business()
        self._build_ops()
        self._build_topology_relations()
        return {
            "workspace": WORKSPACE,
            "window": {
                "start": self.window_start,
                "end": self.window_end,
                "start_iso": iso(self.window_start),
                "end_iso": iso(self.window_end),
            },
            "entities": self.entities,
            "relations": self.relations,
            "metrics": self.snapshot_metrics,
            "timeline": self.declared.get("timeline", []),
            "counts": {
                "entities": len(self.entities),
                "relations": len(self.relations),
            },
        }

    def _build_infra(self) -> None:
        cluster = self.declared.get("cluster") or {
            "name": "prod-shanghai",
            "display": "prod-shanghai",
        }
        nodes = self.declared.get("nodes", [])
        pods = self.declared.get("pods", [])
        workloads = self.declared.get("workloads", [])
        namespaces = self.declared.get("namespaces", [])
        cid = ids.cluster_id(cluster["name"])
        self.entities.append(
            _entity(
                "infra.cluster",
                cid,
                cluster.get("display") or cluster["name"],
                {
                    "region": cluster.get("region", ""),
                    "az_count": cluster.get("az_count", 0),
                    "node_count": len(nodes),
                    "kubernetes_version": cluster.get("kubernetes_version", ""),
                    "status": "healthy",
                },
                self.window_start,
                self.window_end,
            )
        )
        for node in nodes:
            series = self._node_series.get(node["name"], [])
            cpu_values = [
                item.cpu
                for item in series
                if item.time < self.window_start + BASELINE_MINUTES * 60
            ]
            baseline = cpu_values or [node.get("base_cpu", 0.3)]
            current = series[-1].cpu if series else node.get("base_cpu", 0.0)
            z = robust_z(current, baseline, floor_ratio=0.03) if series else 0.0
            onset = None
            run = 0
            for item in series:
                if item.time < self.window_start + BASELINE_MINUTES * 60:
                    continue
                if robust_z(item.cpu, baseline, floor_ratio=0.03) >= ONSET_Z:
                    run += 1
                    if run >= ONSET_CONSECUTIVE:
                        onset = item.time - 60
                        break
                else:
                    run = 0
            status = "critical" if z >= 8 else "warning" if z >= ONSET_Z else "healthy"
            severity = z * min(1.0, max(0.0, current) / 0.8)
            self.entities.append(
                _entity(
                    "infra.node",
                    ids.node_id(node["name"]),
                    node["name"],
                    {
                        "az": node.get("az", ""),
                        "instance_type": node.get("instance_type", ""),
                        "cpu_cores": node.get("cores", 0),
                        "cpu_utilization": round(current, 4),
                        "memory_utilization": round(
                            series[-1].memory if series else 0.0, 4
                        ),
                        "pod_count": sum(
                            1 for pod in pods if pod.get("node") == node["name"]
                        ),
                        "anomaly_score": round(severity, 2),
                        "onset_ts": onset,
                        "status": status,
                    },
                    self.window_start,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "infra.cluster",
                    cid,
                    "infra.node",
                    ids.node_id(node["name"]),
                    "contains",
                    f"{cluster.get('display', cluster['name'])} contains {node['name']}",
                    self.window_start,
                    self.window_end,
                )
            )

        for namespace in namespaces:
            ns_pods = [pod for pod in pods if pod.get("namespace") == namespace["name"]]
            ns_services = [
                svc
                for svc in self.declared.get("services", [])
                if svc.get("namespace") == namespace["name"]
            ]
            self.entities.append(
                _entity(
                    "infra.namespace",
                    ids.namespace_id(namespace["name"]),
                    namespace.get("display") or namespace["name"],
                    {
                        "team": namespace.get("team", ""),
                        "pod_count": len(ns_pods),
                        "service_count": len(ns_services),
                    },
                    self.window_start,
                    self.window_end,
                )
            )

        for workload in workloads:
            self.entities.append(
                _entity(
                    "infra.workload",
                    ids.workload_id(workload["name"]),
                    workload["name"],
                    {
                        "namespace": workload.get("namespace", ""),
                        "kind": workload.get("kind", "Deployment"),
                        "replicas": workload.get("replicas", 0),
                        "ready_replicas": workload.get(
                            "ready", workload.get("replicas", 0)
                        ),
                        "image": workload.get("image", ""),
                        "service": workload.get("service", ""),
                        "status": "healthy",
                    },
                    self.window_start,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "infra.namespace",
                    ids.namespace_id(workload.get("namespace", "")),
                    "infra.workload",
                    ids.workload_id(workload["name"]),
                    "contains",
                    f"{workload.get('namespace', '')} contains {workload['name']}",
                    self.window_start,
                    self.window_end,
                )
            )

        for pod in pods:
            node = next((item for item in nodes if item["name"] == pod.get("node")), {})
            self.entities.append(
                _entity(
                    "infra.pod",
                    ids.pod_id(pod["name"]),
                    pod["name"],
                    {
                        "namespace": pod.get("namespace", ""),
                        "workload": f"{pod.get('service', '')}-deploy",
                        "node": pod.get("node", ""),
                        "az": node.get("az", ""),
                        "ip": pod.get("ip", ""),
                        "phase": "Running",
                        "restart_count": 1
                        if pod.get("service") == "batch-reporter"
                        else 0,
                        "cpu_millicores": 0.0,
                        "memory_mb": 0.0,
                        "status": "healthy",
                    },
                    self.window_start,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "infra.workload",
                    ids.workload_id(f"{pod.get('service', '')}-deploy"),
                    "infra.pod",
                    ids.pod_id(pod["name"]),
                    "contains",
                    f"{pod.get('service', '')}-deploy contains {pod['name']}",
                    self.window_start,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "infra.pod",
                    ids.pod_id(pod["name"]),
                    "infra.node",
                    ids.node_id(pod.get("node", "")),
                    "scheduled_on",
                    f"{pod['name']} scheduled on {pod.get('node', '')}",
                    self.window_start,
                    self.window_end,
                )
            )

    def _build_services(self) -> None:
        changes_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for change in self.declared.get("changes", []):
            changes_by_service[change.get("service", "")].append(change)

        for name, meta in self._service_meta.items():
            in_series = self._service_series.get(name, [])
            out_lists = [
                records
                for (src, dst), records in self._edge_series.items()
                if src == name and records
            ]
            out_series = _merge_series(out_lists)
            in_summary = _metric_summary(in_series, window_start=self.window_start)
            out_summary = _metric_summary(out_series, window_start=self.window_start)
            primary = in_summary if in_summary["observed"] else out_summary
            changes = sorted(
                changes_by_service.get(name, []),
                key=lambda item: item.get("applied_minute", 0),
                reverse=True,
            )
            recent = changes[0] if changes else None
            baseline_out = (
                median(
                    [item.rps for item in out_series if item.minute < BASELINE_MINUTES]
                )
                or 1.0
            )
            out_amp = safe_div(out_summary["rps"], baseline_out, 1.0)
            score = max(in_summary["anomaly_score"], out_summary["anomaly_score"] * 0.7)
            delta = primary["p99_delta_pct"]
            err = primary["err"]
            status = (
                "critical"
                if (
                    (primary["p99"] >= 1000 and delta >= 100)
                    or err >= 0.05
                    or score >= 12
                )
                else "warning"
                if (
                    (primary["p99"] >= 200 and delta >= 50) or err >= 0.01 or score >= 4
                )
                else "healthy"
            )
            onset_ts = in_summary["onset_ts"] or out_summary["onset_ts"]
            onset_signal = (
                in_summary["onset_signal"] or out_summary["onset_signal"] or ""
            )
            payload = {
                "namespace": meta.get("namespace", ""),
                "tier": meta.get("tier", "observed"),
                "language": meta.get("language", "unknown"),
                "owner": meta.get("owner", "unknown"),
                "criticality": meta.get("criticality", "silver"),
                "version": meta.get("version", "observed"),
                "environment": "prod",
                "replicas": meta.get("replicas", 0),
                "qps": primary["rps"],
                "error_rate": primary["err"],
                "latency_p99_ms": primary["p99"],
                "baseline_p99_ms": primary["baseline_p99"],
                "p99_delta_pct": primary["p99_delta_pct"],
                "outbound_qps": out_summary["rps"],
                "outbound_amp": round(out_amp, 2),
                "anomaly_score": round(score, 2),
                "onset_ts": onset_ts,
                "onset_signal": onset_signal,
                "latency_onset_ts": in_summary["onset_ts"],
                "amplification_onset_ts": out_summary["rps_onset_ts"],
                "status": status,
                "role": "unknown",
                "change_risk": recent.get("risk", "") if recent else "",
                "change_id": recent.get("name", "") if recent else "",
                "observation_source": "deepflow.l7_flow_log",
            }
            self.entities.append(
                _entity(
                    "svc.service",
                    ids.service_id(name),
                    meta.get("display") or name,
                    payload,
                    self.window_start,
                    self.window_end,
                )
            )
            if meta.get("workload"):
                self.relations.append(
                    _relation(
                        "svc.service",
                        ids.service_id(name),
                        "infra.workload",
                        ids.workload_id(meta["workload"]),
                        "runs_as",
                        f"{name} runs as {meta['workload']}",
                        self.window_start,
                        self.window_end,
                    )
                )

    def _build_endpoints(self) -> None:
        for key, meta in self._endpoint_meta.items():
            service_name, method, path = key
            # The series key uses the *client* service; endpoint metrics need all
            # clients that call this endpoint, so merge every observed client.
            merged: dict[int, list[MinuteMetric]] = defaultdict(list)
            for (
                client,
                server,
                req_method,
                req_path,
            ), records in self._endpoint_series.items():
                if server == service_name and req_method == method and req_path == path:
                    for item in records:
                        merged[item.minute].append(item)
            merged_series = []
            for minute in sorted(merged):
                items = merged[minute]
                requests = sum(item.requests for item in items)
                if not requests:
                    continue
                merged_series.append(
                    MinuteMetric(
                        minute=minute,
                        rps=requests / 60.0,
                        p99=sum(item.p99 * item.requests for item in items) / requests,
                        err=sum(item.err * item.requests for item in items) / requests,
                        requests=requests,
                        bytes=sum(item.bytes for item in items),
                    )
                )
            summary = _metric_summary(merged_series, window_start=self.window_start)
            status = (
                "critical"
                if (
                    (summary["p99"] >= 1000 and summary["p99_delta_pct"] >= 100)
                    or summary["err"] >= 0.05
                )
                else "warning"
                if (
                    (summary["p99"] >= 200 and summary["p99_delta_pct"] >= 50)
                    or summary["err"] >= 0.01
                )
                else "healthy"
            )
            eid = ids.endpoint_id(service_name, method, path)
            self.entities.append(
                _entity(
                    "svc.endpoint",
                    eid,
                    f"{method} {path}",
                    {
                        "service": service_name,
                        "method": method,
                        "path": path,
                        "protocol": meta.get("protocol", "HTTP"),
                        "qps": summary["rps"],
                        "error_rate": summary["err"],
                        "latency_p99_ms": summary["p99"],
                        "p99_delta_pct": summary["p99_delta_pct"],
                        "anomaly_score": summary["anomaly_score"],
                        "status": status,
                    },
                    self.window_start,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "svc.service",
                    ids.service_id(service_name),
                    "svc.endpoint",
                    eid,
                    "exposes",
                    f"{service_name} exposes {method} {path}",
                    self.window_start,
                    self.window_end,
                )
            )

    def _build_externals(self) -> None:
        for name, meta in self._external_meta.items():
            series = []
            for (src, dst), records in self._edge_series.items():
                if dst == name:
                    series.extend(records)
            series.sort(key=lambda item: item.minute)
            summary = _metric_summary(series, window_start=self.window_start)
            declared = bool(meta.get("declared", True))
            shadow = bool(meta.get("shadow", False)) or not declared
            score = summary["anomaly_score"]
            status = (
                "critical"
                if (
                    (summary["p99"] >= 1000 and summary["p99_delta_pct"] >= 100)
                    or summary["err"] >= 0.05
                )
                else "warning"
                if (
                    (summary["p99"] >= 200 and summary["p99_delta_pct"] >= 50)
                    or summary["err"] >= 0.01
                )
                else "healthy"
            )
            if shadow and summary["observed"]:
                # No baseline exists for a dependency that only appeared during
                # the incident. Its novelty is itself the signal.
                score = max(score, 6.0)
                status = "warning" if status == "healthy" else status
            self.entities.append(
                _entity(
                    "svc.external_service",
                    ids.external_id(name),
                    meta.get("display") or name,
                    {
                        "domain": meta.get("domain", ""),
                        "kind": meta.get("kind", "external"),
                        "provider": meta.get("provider", ""),
                        "criticality": meta.get("criticality", "silver"),
                        "qps": summary["rps"],
                        "error_rate": summary["err"],
                        "latency_p99_ms": summary["p99"],
                        "anomaly_score": score,
                        "declared": declared,
                        "shadow": shadow,
                        "status": status,
                    },
                    self.window_start,
                    self.window_end,
                )
            )

    def _build_business(self) -> None:
        for journey in self.declared.get("journeys", []):
            self.entities.append(
                _entity(
                    "biz.journey",
                    ids.journey_id(journey["name"]),
                    journey.get("display") or journey["name"],
                    {
                        "tier": journey.get("tier", ""),
                        "owner": journey.get("owner", ""),
                        "services": ",".join(journey.get("services", [])),
                        "conversion_impact": journey.get("conversion_impact", ""),
                        "revenue_exposure": journey.get("revenue_exposure", ""),
                        "impacted": False,
                        "impacted_qps": 0.0,
                        "status": "healthy",
                    },
                    self.window_start,
                    self.window_end,
                )
            )
            for service_name in journey.get("services", []):
                self.relations.append(
                    _relation(
                        "biz.journey",
                        ids.journey_id(journey["name"]),
                        "svc.service",
                        ids.service_id(service_name),
                        "depends_on",
                        f"{journey.get('display', journey['name'])} depends on {service_name}",
                        self.window_start,
                        self.window_end,
                    )
                )

    def _build_ops(self) -> None:
        incident = self.declared.get("incident") or {}
        if incident:
            started = self.window_start + int(incident.get("started_minute", 0)) * 60
            detected = (
                self.window_start
                + int(
                    incident.get("detected_minute", incident.get("started_minute", 0))
                )
                * 60
            )
            self.entities.append(
                _entity(
                    "ops.incident",
                    ids.incident_id(incident["name"]),
                    incident.get("display") or incident["name"],
                    {
                        "severity": incident.get("severity", "P3"),
                        "status": incident.get("status", "open"),
                        "started_at": iso(started),
                        "detected_at": iso(detected),
                        "started_ts": started,
                        "victim": incident.get("victim", ""),
                        "summary": incident.get("summary", ""),
                        "impact": incident.get("impact", ""),
                        "owner": incident.get("owner", ""),
                        "root_cause": "",
                    },
                    started,
                    self.window_end,
                )
            )
            victim = incident.get("victim")
            if victim:
                self.relations.append(
                    _relation(
                        "ops.incident",
                        ids.incident_id(incident["name"]),
                        "svc.service",
                        ids.service_id(victim),
                        "affects",
                        f"{incident['name']} affects {victim}",
                        started,
                        self.window_end,
                    )
                )

        for change in self.declared.get("changes", []):
            applied = self.window_start + int(change.get("applied_minute", 0)) * 60
            self.entities.append(
                _entity(
                    "ops.change",
                    ids.change_id(change["name"]),
                    change.get("display") or change["name"],
                    {
                        "service": change.get("service", ""),
                        "change_type": change.get("change_type", ""),
                        "applied_at": iso(applied),
                        "applied_ts": applied,
                        "risk": change.get("risk", ""),
                        "summary": change.get("summary", ""),
                        "detail": change.get("detail", ""),
                        "rollback": change.get("rollback", ""),
                        "status": change.get("status", "applied"),
                    },
                    applied,
                    self.window_end,
                )
            )
            self.relations.append(
                _relation(
                    "ops.change",
                    ids.change_id(change["name"]),
                    "svc.service",
                    ids.service_id(change.get("service", "")),
                    "targets",
                    f"{change['name']} targets {change.get('service', '')}",
                    applied,
                    self.window_end,
                )
            )

        for alert in self.declared.get("alerts", []):
            fired = self.window_start + int(alert.get("fired_minute", 0)) * 60
            self.entities.append(
                _entity(
                    "ops.alert",
                    ids.alert_id(alert["name"]),
                    alert.get("display") or alert["name"],
                    {
                        "target": alert.get("target", ""),
                        "target_type": alert.get("target_type", ""),
                        "severity": alert.get("severity", "P3"),
                        "status": alert.get("status", "firing"),
                        "fired_at": iso(fired),
                        "fired_ts": fired,
                        "signal": alert.get("signal", ""),
                        "value": alert.get("value", ""),
                        "summary": alert.get("summary", ""),
                    },
                    fired,
                    self.window_end,
                )
            )
            target = alert.get("target", "")
            if alert.get("target_type") == "infra.node":
                dest_type, dest_id = "infra.node", ids.node_id(target)
            else:
                dest_type, dest_id = "svc.service", ids.service_id(target)
            self.relations.append(
                _relation(
                    "ops.alert",
                    ids.alert_id(alert["name"]),
                    dest_type,
                    dest_id,
                    "targets",
                    f"{alert['name']} targets {target}",
                    fired,
                    self.window_end,
                )
            )

    def _build_topology_relations(self) -> None:
        declared_edges = {
            (item["src"], item["dst"]): item for item in self.declared.get("edges", [])
        }
        seen: set[tuple[str, str]] = set()
        for (src, dst), records in self._edge_series.items():
            if not records:
                continue
            seen.add((src, dst))
            summary = _metric_summary(records, window_start=self.window_start)
            external = dst in self._external_meta
            dest_type = "svc.external_service" if external else "svc.service"
            dest_id = ids.external_id(dst) if external else ids.service_id(dst)
            decl = declared_edges.get((src, dst))
            declared = bool(decl.get("declared", True)) if decl else False
            shadow = bool(decl.get("shadow", False)) if decl else external
            first = self.window_start + max(0, records[0].minute) * 60
            last = self.window_start + max(0, records[-1].minute) * 60
            self.relations.append(
                _relation(
                    "svc.service",
                    ids.service_id(src),
                    dest_type,
                    dest_id,
                    "calls",
                    f"{src} calls {dst}",
                    first,
                    last,
                    {
                        "rps": summary["rps"],
                        "error_rate": summary["err"],
                        "latency_p99_ms": summary["p99"],
                        "baseline_p99_ms": summary["baseline_p99"],
                        "p99_delta_pct": summary["p99_delta_pct"],
                        "onset_ts": summary["onset_ts"],
                        "observed": True,
                        "declared": declared,
                        "shadow": shadow,
                        "evidence": "deepflow.l7_flow_log",
                        "confidence": 0.98,
                        "requests_per_min": summary["requests"],
                        "bytes_per_min": summary["bytes"],
                    },
                )
            )

        # Declared but unobserved edges stay in the graph as zero-traffic edges.
        for (src, dst), decl in declared_edges.items():
            if (src, dst) in seen:
                continue
            if not decl.get("declared", True):
                continue
            external = dst in self._external_meta
            dest_type = "svc.external_service" if external else "svc.service"
            dest_id = ids.external_id(dst) if external else ids.service_id(dst)
            self.relations.append(
                _relation(
                    "svc.service",
                    ids.service_id(src),
                    dest_type,
                    dest_id,
                    "calls",
                    f"{src} calls {dst} (declared, unobserved)",
                    self.window_start,
                    self.window_end,
                    {
                        "rps": 0.0,
                        "error_rate": 0.0,
                        "latency_p99_ms": 0.0,
                        "baseline_p99_ms": 0.0,
                        "p99_delta_pct": 0.0,
                        "onset_ts": None,
                        "observed": False,
                        "declared": True,
                        "shadow": False,
                        "evidence": "declared_topology",
                        "confidence": 0.0,
                    },
                )
            )

        # Endpoint-level call evidence for internal calls.
        for (client, server, method, path), records in self._endpoint_series.items():
            summary = _metric_summary(records, window_start=self.window_start)
            self.relations.append(
                _relation(
                    "svc.service",
                    ids.service_id(client),
                    "svc.endpoint",
                    ids.endpoint_id(server, method, path),
                    "calls_endpoint",
                    f"{client} calls {server} {method} {path}",
                    self.window_start + max(0, records[0].minute) * 60,
                    self.window_start + max(0, records[-1].minute) * 60,
                    {
                        "rps": summary["rps"],
                        "error_rate": summary["err"],
                        "latency_p99_ms": summary["p99"],
                        "baseline_p99_ms": summary["baseline_p99"],
                        "p99_delta_pct": summary["p99_delta_pct"],
                        "onset_ts": summary["onset_ts"],
                        "observed": True,
                        "declared": True,
                        "evidence": "deepflow.l7_flow_log",
                        "confidence": 0.98,
                    },
                )
            )

        self.snapshot_metrics = {
            "edge_count": len(self._edge_series),
            "endpoint_edge_count": len(self._endpoint_series),
            "service_count": len(self._service_meta),
            "flow_records": len(self.flows),
        }
