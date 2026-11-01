"""End-to-end pipeline: replay -> UModel -> analysis -> artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis import analyze, changed_entities
from .config import (
    ARTIFACT_DIR,
    MODEL_DIR,
    REPLAY_DIR,
    WINDOW_END,
    WINDOW_MINUTES,
    WINDOW_START,
    WORKSPACE,
)
from .deepflow import load_dataset
from .materialize import Materializer
from .model_pack import build_model_pack
from .ontology import ENTITY_SETS
from .scenario import generate_replay
from .umodel_client import UModelClient
from .util import write_json, write_text


def load_snapshot_from_umodel(
    client: UModelClient, declared: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Read the whole graph back through the public Query Service."""

    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, item in ENTITY_SETS.items():
        rows = client.entities(item["domain"], name, limit=5000)
        for row in rows:
            eid = str(row.get("__entity_id__") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            entities.append(row)
    relations = client.all_relations()
    return {
        "workspace": client.workspace,
        "window": {
            "start": WINDOW_START,
            "end": WINDOW_END,
        },
        "entities": entities,
        "relations": relations,
        "metrics": {},
        "timeline": (declared or {}).get("timeline", []),
        "counts": {"entities": len(entities), "relations": len(relations)},
    }


def run_pipeline(
    addr: str,
    workspace: str = WORKSPACE,
    source: str = "replay",
    replay_dir: str | Path | None = None,
    deepflow_base: str | None = None,
    start: int | None = None,
    end: int | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    replay = generate_replay(Path(replay_dir) if replay_dir else REPLAY_DIR)
    pack = build_model_pack(MODEL_DIR)
    client = UModelClient(addr, workspace)
    if not client.wait_ready():
        raise RuntimeError(f"UModel is not ready at {addr}")
    workspace_result = client.ensure_workspace()

    imports = {}
    for sub in ("entity_set", "link"):
        imports[sub] = client.import_model(str(MODEL_DIR / sub))

    window_start = start or WINDOW_START
    window_end = end or (window_start + WINDOW_MINUTES * 60)
    flows, nodes, declared = load_dataset(
        source,
        replay_dir=replay_dir,
        deepflow_base=deepflow_base,
        start=window_start,
        end=window_end,
    )
    snapshot = Materializer(flows, nodes, declared, window_start=window_start).build()
    snapshot["workspace"] = workspace
    snapshot["window"]["end"] = window_end
    entity_write = client.write_entities(snapshot["entities"])
    relation_write = client.write_relations(snapshot["relations"])
    analysis = analyze(snapshot)
    role_write = client.write_entities(changed_entities(snapshot))

    umodel_snapshot = load_snapshot_from_umodel(client, declared)
    verification = {
        "declared_entities": len(snapshot["entities"]),
        "umodel_entities": umodel_snapshot["counts"]["entities"],
        "declared_relations": len(snapshot["relations"]),
        "umodel_relations": umodel_snapshot["counts"]["relations"],
        "root_cause_field": next(
            (
                item.get("root_cause")
                for item in umodel_snapshot["entities"]
                if item.get("__entity_type__") == "ops.incident"
            ),
            "",
        ),
        "service_roles": {
            str(item.get("display_name")): item.get("role")
            for item in umodel_snapshot["entities"]
            if item.get("__entity_type__") == "svc.service"
        },
    }
    result = {
        "pipeline": {
            "workspace": workspace,
            "workspace_result": workspace_result,
            "imports": imports,
            "entity_write": entity_write,
            "relation_write": relation_write,
            "role_write": role_write,
            "replay": replay,
            "pack": pack,
        },
        "snapshot": snapshot,
        "analysis": analysis,
        "umodel": umodel_snapshot,
        "verification": verification,
    }
    if write_artifacts:
        write_artifacts_bundle(result)
    return result


def write_artifacts_bundle(result: dict[str, Any]) -> dict[str, str]:
    root = ARTIFACT_DIR
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = write_json(root / "snapshot.json", result["snapshot"])
    analysis_path = write_json(root / "analysis.json", result["analysis"])
    verification_path = write_json(root / "verification.json", result["verification"])
    report_path = write_text(root / "incident-report.md", render_report(result))
    graph_path = write_json(root / "graph.json", build_graph_document(result))
    return {
        "snapshot": str(snapshot_path),
        "analysis": str(analysis_path),
        "verification": str(verification_path),
        "report": str(report_path),
        "graph": str(graph_path),
    }


def build_graph_document(result: dict[str, Any]) -> dict[str, Any]:
    """A compact graph document that is independent of the console."""

    snapshot = result["snapshot"]
    analysis = result["analysis"]
    return {
        "nodes": [
            {
                "id": item.get("__entity_id__"),
                "type": item.get("__entity_type__"),
                "name": item.get("display_name"),
                "status": item.get("status"),
                "role": item.get("role"),
                "metrics": {
                    "qps": item.get("qps"),
                    "p99_ms": item.get("latency_p99_ms"),
                    "error_rate": item.get("error_rate"),
                },
            }
            for item in snapshot["entities"]
        ],
        "edges": [
            {
                "src": item.get("__src_entity_id__"),
                "dst": item.get("__dest_entity_id__"),
                "type": item.get("__relation_type__"),
                "rps": item.get("rps"),
                "p99_ms": item.get("latency_p99_ms"),
                "error_rate": item.get("error_rate"),
                "observed": item.get("observed"),
                "declared": item.get("declared"),
                "shadow": item.get("shadow"),
            }
            for item in snapshot["relations"]
        ],
        "root_cause": analysis["root_cause"],
        "victim": analysis["victim"],
        "path": analysis["path"],
    }


def render_report(result: dict[str, Any]) -> str:
    analysis = result["analysis"]
    snapshot = result["snapshot"]
    incident = next(
        (
            item
            for item in snapshot["entities"]
            if item.get("__entity_type__") == "ops.incident"
        ),
        {},
    )
    ranking = analysis["ranking"]
    lines = [
        f"# 故障报告：{incident.get('display_name', 'Operational incident')}",
        "",
        f"- 事件：`{incident.get('display_name', '')}`（{incident.get('severity', '')}，{incident.get('status', '')}）",
        f"- 受害服务：`{analysis['victim']}`",
        f"- 根因：`{analysis['root_cause']}`（置信度 {analysis['confidence'] * 100:.0f}%）",
        f"- 时间窗口：{snapshot['window'].get('start_iso', '')} → {snapshot['window'].get('end_iso', '')}",
        f"- 图规模：{snapshot['counts']['entities']} 个实体 / {snapshot['counts']['relations']} 条关系",
        "",
        "## 结论",
        "",
        analysis["summary"],
        "",
        f"因果路径：`{' → '.join(analysis['path'])}`",
        "",
        "## 根因排序",
        "",
        "| # | 服务 | 得分 | 自身异常 | 变更关联 | 出向放大 | 因果领先 | 图位置 | 关键证据 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in ranking[:6]:
        c = item["components"]
        evidence = "；".join(item["reasons"][:3])
        lines.append(
            f"| {item['rank']} | `{item['service']}` | {item['score']:.2f} | {c['self_severity']:.3f} | {c['change_affinity']:.3f} | "
            f"{c['outbound_amplification']:.3f} | {c['causal_lead']:.3f} | {c['graph_position']:.3f} | {evidence} |"
        )
    lines += [
        "",
        "## 时间线",
        "",
        "| 时间 | 类型 | 事件 | 说明 |",
        "|---|---|---|---|",
    ]
    for event in analysis["timeline"]:
        lines.append(
            f"| {event['clock']} | {event['kind']} | {event['title']} | {event['detail']} |"
        )
    lines += [
        "",
        "## 影响面",
        "",
        f"- {analysis['impact']['summary']}",
        f"- 受影响服务：{', '.join(item['service'] for item in analysis['impact']['services'])}",
    ]
    for journey in analysis["impact"]["journeys"]:
        lines.append(
            f"- 业务旅程 `{journey['journey']}`（{journey['tier']}）：{journey['conversion_impact']}，营收敞口 {journey['revenue_exposure']}"
        )
    lines += ["", "## 图工程发现", ""]
    for finding in analysis["findings"]:
        lines.append(
            f"- **[{finding['severity']}] {finding['title']}**：{finding['detail']}"
        )
    lines += ["", "## 已排除", ""]
    for item in analysis["ruled_out"]:
        lines.append(f"- **{item['title']}**：{item['detail']}")
    lines += [
        "",
        "## 复现查询",
        "",
        "```bash",
        f"umctl --addr http://127.0.0.1:18080 query run {result['pipeline']['workspace']} \\",
        "  \".entity with(domain='svc', name='svc.service', query='checkout-service')\"",
        "",
        f"umctl --addr http://127.0.0.1:18080 query run {result['pipeline']['workspace']} \\",
        '  ".topo | graph-call getDirectRelations([(:\\"svc@svc.service\\" {__entity_id__: \'<checkout-id>\'})])"',
        "```",
        "",
        "模型、实体与关系均通过 UModel 公共 REST/CLI 契约写入和读取；eBPF 证据字段挂在 `calls` 关系上。",
    ]
    return "\n".join(lines) + "\n"
