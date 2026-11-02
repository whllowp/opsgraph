"""Command line interface for OpsGraph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze, changed_entities
from .config import ARTIFACT_DIR, MODEL_DIR, REPLAY_DIR, UMODEL_ADDR, WORKSPACE
from .console_server import serve
from .model_pack import build_model_pack
from .pipeline import load_snapshot_from_umodel, run_pipeline
from .scenario import generate_replay
from .umodel_client import UModelClient
from .util import ensure_utf8_stdout, read_json, write_json


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_model(args: argparse.Namespace) -> int:
    result = build_model_pack(Path(args.output) if args.output else MODEL_DIR)
    _print_json(result)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    result = generate_replay(Path(args.output) if args.output else REPLAY_DIR)
    _print_json(result)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    result = run_pipeline(
        addr=args.addr,
        workspace=args.workspace,
        source=args.source,
        deepflow_base=args.deepflow_base,
        start=args.start,
        end=args.end,
        agentsight_path=args.agentsight,
        include_agents=not args.no_agents,
        history_path=args.history,
    )
    analysis = result["analysis"]
    print("=" * 78)
    print("OpsGraph demo complete")
    print("=" * 78)
    print(f"UModel:          {args.addr}  workspace={args.workspace}")
    print(f"Entities:        {result['pipeline']['entity_write']['accepted']} written")
    print(f"Relations:       {result['pipeline']['relation_write']['accepted']} written")
    print(
        f"Root cause:      {analysis['root_cause']}  (confidence {analysis['confidence'] * 100:.0f}%)"
    )
    print(f"Victim:          {analysis['victim']}")
    print(f"Path:            {' -> '.join(analysis['path'])}")
    print(f"Summary:         {analysis['summary']}")
    print(
        f"Findings:        {len(analysis['findings'])}  ({', '.join(item['id'] for item in analysis['findings'])})"
    )
    agents = result.get("agents") or {}
    if agents:
        summary = agents.get("summary") or {}
        print(
            f"Agents:          {summary.get('sessions', 0)} sessions · {summary.get('tool_calls', 0)} tool calls · "
            f"{summary.get('total_tokens', 0):,} tokens · ${summary.get('cost_usd', 0):.2f} · "
            f"{summary.get('retry_loops', 0)} retry loops · {summary.get('delegations', 0)} delegations"
        )
        baseline = result.get("baseline") or {}
        accuracy = (baseline.get("claim_accuracy") or {}).get("overall") or {}
        if baseline:
            print(
                f"Baseline:        {baseline.get('history_runs', 0)} historical runs · "
                f"claim accuracy {accuracy.get('correct', 0)}/{accuracy.get('total', 0)}"
            )
        for decision in result.get("policy") or []:
            print(
                f"Gate:            {decision.get('decision'):<16} {decision.get('tool')} {decision.get('target')}"
            )
    print(f"Artifacts:       {ARTIFACT_DIR}")
    print(
        f"Verification:    entities {result['verification']['declared_entities']} -> {result['verification']['umodel_entities']}, "
        f"relations {result['verification']['declared_relations']} -> {result['verification']['umodel_relations']}"
    )
    if result["verification"]["root_cause_field"] != analysis["root_cause"]:
        print("WARNING: root_cause field read back from UModel does not match the analysis")
        return 1
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    source = args.source
    declared = None
    if (REPLAY_DIR / "declared_topology.json").exists():
        from .deepflow import load_declared_topology

        declared = load_declared_topology()
    if source == "umodel":
        client = UModelClient(args.addr, args.workspace)
        if not client.wait_ready():
            print(f"UModel is not ready at {args.addr}", file=sys.stderr)
            return 1
        snapshot = load_snapshot_from_umodel(client, declared)
    else:
        path = ARTIFACT_DIR / "snapshot.json"
        if not path.exists():
            print(f"snapshot not found: {path}", file=sys.stderr)
            return 1
        snapshot = read_json(path)
    analysis = analyze(snapshot)
    write_json(ARTIFACT_DIR / "snapshot.json", snapshot)
    write_json(ARTIFACT_DIR / "analysis.json", analysis)
    if source == "umodel":
        client = UModelClient(args.addr, args.workspace)
        client.write_entities(changed_entities(snapshot))
    _print_json(
        {
            "root_cause": analysis["root_cause"],
            "victim": analysis["victim"],
            "confidence": analysis["confidence"],
            "summary": analysis["summary"],
            "ranking": [
                {"service": item["service"], "score": item["score"]}
                for item in analysis["ranking"][:5]
            ],
            "findings": [item["id"] for item in analysis["findings"]],
        }
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    serve(args.host, args.port)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    client = UModelClient(args.addr, args.workspace)
    rows = client.query(args.spl)
    _print_json({"count": len(rows), "rows": rows})
    return 0


def cmd_rca(args: argparse.Namespace) -> int:
    path = ARTIFACT_DIR / "analysis.json"
    if not path.exists():
        print(
            "analysis.json not found; run `python -m opsgraph demo` first",
            file=sys.stderr,
        )
        return 1
    analysis = read_json(path)
    for item in analysis["ranking"]:
        print(
            f"{item['rank']:>2}. {item['service']:<24} score={item['score']:.3f}  {item['status']}"
        )
        for reason in item["reasons"][:4]:
            print(f"     - {reason}")
    print()
    print(analysis["summary"])
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    client = UModelClient(args.addr, args.workspace)
    result: dict[str, object] = {"addr": args.addr, "workspace": args.workspace}
    try:
        result["health"] = client.health()
        result["workspace_exists"] = client.workspace_exists()
        result["model"] = client.model_count()
        result["entities"] = len(client.entities("svc", "svc.service", 1000))
        result["relations"] = len(client.all_relations())
        result["status"] = "ok"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    _print_json(result)
    return 0 if result.get("status") == "ok" else 1


def cmd_code_demo(args: argparse.Namespace) -> int:
    from .code_rca import run_rea_loop, write_rea_artifacts, write_telemetry

    telemetry_path = write_telemetry()
    result = run_rea_loop(max_iterations=args.iterations)
    paths = write_rea_artifacts(result, ARTIFACT_DIR)
    summary = result["summary"]
    print("=" * 78)
    print("Code RCA / REA demo complete")
    print("=" * 78)
    print(f"Telemetry:       {telemetry_path}")
    print(f"Iterations:      {summary['iterations']}")
    print(f"Fixed:           {summary['fixed']}  ({summary['success_rate'] * 100:.0f}%)")
    print(f"RCA matches:     {summary['rca_matches']}/{summary['iterations']}")
    print(f"Final suite:     {'PASS' if summary['final_suite']['passed'] else 'FAIL'}")
    print(f"Headline:        {summary['headline']}")
    for item in result["iterations"]:
        finding = item["rca"]["finding"]
        print(
            f"  {item['iteration']:>2}. {item['case_id']}  "
            f"{finding.get('file')}:{finding.get('line')} {finding.get('function')}  "
            f"{item['verification']['p99_before_ms']}ms -> {item['verification']['p99_after_ms']}ms  "
            f"{item['status']}"
        )
    print(f"Artifacts:       {paths['json']}")
    print(f"Report:          {paths['report']}")
    return 0 if summary["failed"] == 0 else 1


def cmd_gate(args: argparse.Namespace) -> int:
    from .policy import evaluate_gate

    snapshot_path = ARTIFACT_DIR / "snapshot.json"
    agents_path = ARTIFACT_DIR / "agents.json"
    if not snapshot_path.exists() or not agents_path.exists():
        print("artifacts not found; run `python -m opsgraph demo` first", file=sys.stderr)
        return 1
    result = evaluate_gate(
        read_json(snapshot_path),
        read_json(agents_path),
        args.session,
        args.tool,
        args.target,
        args.command or "",
    )
    _print_json(result)
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from .history import compute_baseline, load_history

    agents_path = ARTIFACT_DIR / "agents.json"
    if not agents_path.exists():
        print("agents.json not found; run `python -m opsgraph demo` first", file=sys.stderr)
        return 1
    result = compute_baseline(load_history(args.history), read_json(agents_path))
    _print_json(result)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    snapshot_path = ARTIFACT_DIR / "snapshot.json"
    analysis_path = ARTIFACT_DIR / "analysis.json"
    if not snapshot_path.exists() or not analysis_path.exists():
        print("artifacts not found; run `python -m opsgraph demo` first", file=sys.stderr)
        return 1
    from .pipeline import render_report

    result = {
        "snapshot": read_json(snapshot_path),
        "analysis": read_json(analysis_path),
        "pipeline": {"workspace": args.workspace},
    }
    agents_path = ARTIFACT_DIR / "agents.json"
    if agents_path.exists():
        result["agents"] = read_json(agents_path)
    baseline_path = ARTIFACT_DIR / "baseline.json"
    if baseline_path.exists():
        result["baseline"] = read_json(baseline_path)
    policy_path = ARTIFACT_DIR / "policy.json"
    if policy_path.exists():
        result["policy"] = read_json(policy_path)
    target = ARTIFACT_DIR / "incident-report.md"
    target.write_text(render_report(result), encoding="utf-8")
    print(target)
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--addr", default=argparse.SUPPRESS, help="UModel address")
    parser.add_argument("--workspace", default=argparse.SUPPRESS, help="UModel workspace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opsgraph",
        description="eBPF-observed operational graphs, modeled in UModel",
    )
    parser.add_argument("--addr", default=UMODEL_ADDR, help="UModel address")
    parser.add_argument("--workspace", default=WORKSPACE, help="UModel workspace")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="generate replay, import model, write graph, analyze")
    _add_common(p)
    p.add_argument("--source", choices=["replay", "deepflow"], default="replay")
    p.add_argument(
        "--deepflow-base",
        default=None,
        help="DeepFlow server URL (default: DEEPFLOW_API_BASE)",
    )
    p.add_argument("--start", type=int, default=None, help="window start (unix seconds)")
    p.add_argument("--end", type=int, default=None, help="window end (unix seconds)")
    p.add_argument("--agentsight", default=None, help="AgentSight snapshot.json or agentsight-*.db")
    p.add_argument("--no-agents", action="store_true", help="skip AgentSight AI graph")
    p.add_argument("--history", default=None, help="historical agent runs JSON")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("analyze", help="re-run graph analysis")
    _add_common(p)
    p.add_argument("--source", choices=["snapshot", "umodel"], default="snapshot")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("serve", help="serve the graph console")
    _add_common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18088)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("query", help="run an SPL query against UModel")
    _add_common(p)
    p.add_argument("spl")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("rca", help="print the root-cause ranking from artifacts")
    _add_common(p)
    p.set_defaults(func=cmd_rca)

    p = sub.add_parser("model", help="generate the UModel model pack")
    _add_common(p)
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("replay", help="generate the deterministic DeepFlow replay")
    _add_common(p)
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("doctor", help="check the UModel connection and graph counts")
    _add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("code-demo", help="run eBPF code-level RCA and 10-iteration REA loop")
    _add_common(p)
    p.add_argument("--iterations", type=int, default=10)
    p.set_defaults(func=cmd_code_demo)

    p = sub.add_parser("gate", help="evaluate a proposed agent action before execution")
    _add_common(p)
    p.add_argument("--session", required=True, help="agent session key or display name")
    p.add_argument("--tool", required=True, help="proposed tool name")
    p.add_argument("--target", required=True, help="target service/path/command")
    p.add_argument("--command", default="", help="optional shell command")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("baseline", help="compute agent historical baseline and claim accuracy")
    _add_common(p)
    p.add_argument("--history", default=None, help="historical agent runs JSON")
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("report", help="regenerate the markdown incident report")
    _add_common(p)
    p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
