"""End-to-end pipeline: replay -> UModel -> analysis -> artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_graph import AgentGraphBuilder
from .agentsight import load_agentsight
from .agentsight_demo import generate_demo_snapshot
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
    agentsight_path: str | Path | None = None,
    include_agents: bool = True,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    replay = generate_replay(Path(replay_dir) if replay_dir else REPLAY_DIR)
    agent_path: Path | None = None
    if include_agents:
        if agentsight_path is None:
            agent_path = generate_demo_snapshot(
                Path(replay_dir or REPLAY_DIR) / "agentsight_snapshot.json"
            )
        else:
            agent_path = Path(agentsight_path)
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
    analysis = analyze(snapshot)

    agent_snapshot = load_agentsight(agent_path) if agent_path else None
    agent_analysis: dict[str, Any] | None = None
    if agent_snapshot:
        agent_entities, agent_relations, agent_analysis = AgentGraphBuilder(
            snapshot, agent_snapshot, analysis
        ).build()
        snapshot["entities"].extend(agent_entities)
        snapshot["relations"].extend(agent_relations)
        snapshot["counts"] = {
            "entities": len(snapshot["entities"]),
            "relations": len(snapshot["relations"]),
        }

    entity_write = client.write_entities(snapshot["entities"])
    reconcile = reconcile_ai_relations(client, snapshot["relations"])
    relation_write = client.write_relations(snapshot["relations"])
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
        "agent_sessions": len(
            [
                item
                for item in umodel_snapshot["entities"]
                if item.get("__entity_type__") == "ai.agent_session"
            ]
        ),
        "agent_tool_calls": len(
            [
                item
                for item in umodel_snapshot["entities"]
                if item.get("__entity_type__") == "ai.tool_call"
            ]
        ),
        "agent_effects": len(
            [
                item
                for item in umodel_snapshot["entities"]
                if item.get("__entity_type__") == "ai.effect"
            ]
        ),
    }
    result = {
        "pipeline": {
            "workspace": workspace,
            "workspace_result": workspace_result,
            "imports": imports,
            "entity_write": entity_write,
            "relation_write": relation_write,
            "relation_reconcile": reconcile,
            "role_write": role_write,
            "replay": replay,
            "pack": pack,
            "agentsight": str(agent_path) if agent_path else None,
        },
        "snapshot": snapshot,
        "analysis": analysis,
        "agents": agent_analysis,
        "umodel": umodel_snapshot,
        "verification": verification,
    }
    if write_artifacts:
        write_artifacts_bundle(result)
    return result


def _relation_key(relation: dict[str, Any]) -> str:
    return "/".join(
        [
            str(relation.get("__src_domain__") or ""),
            str(relation.get("__src_entity_type__") or ""),
            str(relation.get("__src_entity_id__") or ""),
            str(relation.get("__relation_type__") or ""),
            str(relation.get("__dest_domain__") or ""),
            str(relation.get("__dest_entity_type__") or ""),
            str(relation.get("__dest_entity_id__") or ""),
        ]
    )


def reconcile_ai_relations(
    client: UModelClient, current_relations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Expire AI relations that disappeared between two pipeline runs.

    UModel writes are upserts; a changed tool->effect match otherwise leaves a
    stale ``produces`` edge in the graph forever. Only AI-domain relations are
    reconciled here so service topology can evolve independently.
    """

    current = {_relation_key(item) for item in current_relations}
    stale = []
    try:
        existing = client.all_relations()
    except Exception:
        return {"expired": 0, "error": "could not enumerate existing relations"}
    for relation in existing:
        if relation.get("__src_domain__") != "ai" and relation.get("__dest_domain__") != "ai":
            continue
        key = _relation_key(relation)
        if key and key not in current:
            stale.append(key)
    if not stale:
        return {"expired": 0, "candidates": 0}
    result = client.expire_relations(stale)
    return {"expired": result.get("accepted", 0), "candidates": len(stale)}


def write_artifacts_bundle(result: dict[str, Any]) -> dict[str, str]:
    root = ARTIFACT_DIR
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = write_json(root / "snapshot.json", result["snapshot"])
    analysis_path = write_json(root / "analysis.json", result["analysis"])
    agents_path = write_json(root / "agents.json", result.get("agents") or {})
    verification_path = write_json(root / "verification.json", result["verification"])
    report_path = write_text(root / "incident-report.md", render_report(result))
    graph_path = write_json(root / "graph.json", build_graph_document(result))
    return {
        "snapshot": str(snapshot_path),
        "analysis": str(analysis_path),
        "agents": str(agents_path),
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
        (item for item in snapshot["entities"] if item.get("__entity_type__") == "ops.incident"),
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
        lines.append(f"- **[{finding['severity']}] {finding['title']}**：{finding['detail']}")
    lines += ["", "## 已排除", ""]
    for item in analysis["ruled_out"]:
        lines.append(f"- **{item['title']}**：{item['detail']}")
    agents = result.get("agents") or {}
    if agents:
        summary = agents.get("summary") or {}
        lines += [
            "",
            "## AI Agent 观测（AgentSight）",
            "",
            f"- 会话：{summary.get('sessions', 0)}；工具调用：{summary.get('tool_calls', 0)}；失败：{summary.get('failed_tool_calls', 0)}；重试环：{summary.get('retry_loops', 0)}",
            f"- Token：{summary.get('total_tokens', 0):,}；成本：${summary.get('cost_usd', 0):.2f}；高风险效果：{summary.get('high_risk_effects', 0)}",
            "",
            "| Agent 会话 | 模型 | Tokens | 成本 | 工具/失败 | 重试环 | 风险 | 关联服务 | 结论校验 |",
            "|---|---|---:|---:|---:|---:|---|---|---|",
        ]
        for session in agents.get("sessions", []):
            lines.append(
                f"| `{session['name']}` | {session.get('model', '')} | {int(session.get('total_tokens') or 0):,} | "
                f"${float(session.get('cost_usd') or 0):.2f} | {session.get('tool_calls', 0)}/{session.get('failed_tool_calls', 0)} | "
                f"{session.get('retry_loops', 0)} | {session.get('risk_level', '')} | {session.get('linked_service', '') or '-'} | "
                f"{session.get('claim_status', '') or '-'} |"
            )
        lines.append("")
        for finding in agents.get("findings", []):
            lines.append(
                f"- **[{finding.get('severity')}] {finding.get('title')}**：{finding.get('detail')}"
            )
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
        "模型、实体与关系均通过 UModel 公共 REST/CLI 契约写入和读取；`calls` 边携带 eBPF 证据，`ai.*` 边携带 AgentSight 证据。",
    ]
    return "\n".join(lines) + "\n"
