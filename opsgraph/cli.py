"""Command line interface for OpsGraph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze, changed_entities
from .approvals import ApprovalStore
from .brief import build_brief, render_brief_markdown
from .brief_history import ActionLedger
from .codeowners import OwnersIndex
from .config import ARTIFACT_DIR, MODEL_DIR, PROJECT_ROOT, REPLAY_DIR, UMODEL_ADDR, WORKSPACE
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


def cmd_code_ingest(args: argparse.Namespace) -> int:
    from .code_cases import CASE_BY_ID
    from .code_rca import analyze_code
    from .ebpf_stacks import load_stacks, parse_collapsed, parse_perf_script

    case = CASE_BY_ID.get(args.case)
    if not case:
        print(f"unknown case: {args.case}", file=sys.stderr)
        return 1
    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    if args.format == "folded":
        samples = parse_collapsed(text, case_id=case["id"])
    elif args.format == "perf":
        samples = parse_perf_script(text, case_id=case["id"])
    else:
        samples = load_stacks(args.input, case_id=case["id"])
    rca = analyze_code(samples, args.repo, case)
    _print_json(
        {
            "source": "ebpf.external",
            "input": args.input,
            "case": case["id"],
            "samples": len(samples),
            "finding": rca["finding"],
            "ranking": rca["ranking"][:3],
        }
    )
    return 0


def cmd_code_demo(args: argparse.Namespace) -> int:
    from .code_cases import FINTECH_ROOT
    from .code_graph import build_code_graph
    from .code_rca import run_rea_loop, write_rea_artifacts, write_telemetry
    from .model_pack import build_model_pack
    from .umodel_client import UModelClient

    telemetry_path = write_telemetry()
    result = run_rea_loop(max_iterations=args.iterations)
    snapshot_path = ARTIFACT_DIR / "snapshot.json"
    snapshot = read_json(snapshot_path) if snapshot_path.exists() else {}
    entities, relations, graph_summary = build_code_graph(result, FINTECH_ROOT, snapshot)
    result["code_graph"] = graph_summary
    paths = write_rea_artifacts(result, ARTIFACT_DIR)
    write_json(
        ARTIFACT_DIR / "code_graph.json",
        {"summary": graph_summary, "entities": entities, "relations": relations},
    )
    umodel = {"status": "skipped"}
    if not args.no_umodel:
        client = UModelClient(args.addr, args.workspace)
        try:
            if client.health().get("status") == "ok":
                build_model_pack(MODEL_DIR)
                client.import_model(str(MODEL_DIR / "entity_set"))
                client.import_model(str(MODEL_DIR / "link"))
                umodel = {
                    "status": "ok",
                    "entity_write": client.write_entities(entities),
                    "relation_write": client.write_relations(relations),
                }
            else:
                umodel = {"status": "umodel-unavailable"}
        except Exception as exc:
            umodel = {"status": "skipped", "error": str(exc)}
    result["umodel"] = umodel
    summary = result["summary"]
    print("=" * 78)
    print("Code RCA / REA demo complete")
    print("=" * 78)
    print(f"Telemetry:       {telemetry_path}")
    print(f"Iterations:      {summary['iterations']}")
    print(f"Fixed:           {summary['fixed']}  ({summary['success_rate'] * 100:.0f}%)")
    print(f"RCA matches:     {summary['rca_matches']}/{summary['iterations']}")
    print(
        f"Candidates:      {summary.get('candidate_attempts', 0)} "
        f"({summary.get('rejected_candidates', 0)} rejected by tests)"
    )
    print(f"Final suite:     {'PASS' if summary['final_suite']['passed'] else 'FAIL'}")
    print(
        f"Code graph:      {graph_summary['entities']} entities / {graph_summary['relations']} relations"
    )
    print(f"UModel:          {umodel.get('status')}")
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


def cmd_real_perf(args: argparse.Namespace) -> int:
    from .real_perf import run_real_perf_rea, write_real_perf_artifacts

    result = run_real_perf_rea(max_iterations=args.iterations)
    if result.get("status") == "skipped":
        print(f"real perf skipped: {result.get('reason')}")
        return 0
    paths = write_real_perf_artifacts(result, ARTIFACT_DIR)
    print("=" * 78)
    print("Real perf RCA / REA complete")
    print("=" * 78)
    print(f"perf:            {result.get('perf')}")
    print(f"Cases:           {result.get('cases')}")
    print(f"Fixed:           {result.get('fixed')}  ({result.get('success_rate') * 100:.0f}%)")
    print(f"Before all:      {'PASS' if result.get('before_all', {}).get('passed') else 'FAIL'}")
    print(f"Final all:       {'PASS' if result.get('final_all', {}).get('passed') else 'FAIL'}")
    for item in result.get("iterations", []):
        finding = item["rca"]["finding"]
        print(
            f"  {item['iteration']:>2}. {item['case_id']}  "
            f"{finding.get('file')}:{finding.get('line')} {finding.get('function')}  "
            f"samples={item['before_capture'].get('samples')}  {item['status']}"
        )
    print(f"Artifacts:       {paths['json']}")
    print(f"Report:          {paths['report']}")
    return 0 if result.get("fixed") == result.get("cases") else 1


def cmd_brief(args: argparse.Namespace) -> int:
    snapshot_path = ARTIFACT_DIR / "snapshot.json"
    analysis_path = ARTIFACT_DIR / "analysis.json"
    if not snapshot_path.exists() or not analysis_path.exists():
        print("artifacts not found; run `python -m opsgraph demo` first", file=sys.stderr)
        return 1
    optional = {}
    for name in ("agents", "code_rca", "code_graph", "real_perf_rea"):
        path = ARTIFACT_DIR / f"{name}.json"
        optional[name] = read_json(path) if path.exists() else {}
    policy_path = ARTIFACT_DIR / "policy.json"
    policy = read_json(policy_path) if policy_path.exists() else []
    approvals = ApprovalStore(ARTIFACT_DIR / "approvals.json")
    approvals.ensure_from_policy(policy)
    snapshot = read_json(snapshot_path)
    brief = build_brief(
        snapshot,
        read_json(analysis_path),
        optional["agents"],
        policy,
        optional["code_rca"],
        optional["code_graph"],
        real_perf=optional["real_perf_rea"],
        codeowners=OwnersIndex(PROJECT_ROOT),
        approvals=approvals.requests,
    )
    ledger = ActionLedger(ARTIFACT_DIR / "brief_history.jsonl")
    brief = ledger.reconcile(
        brief,
        snapshot,
        approvals=approvals.requests,
        code_rca=optional["code_rca"],
    )
    write_json(ARTIFACT_DIR / "brief.json", brief)
    (ARTIFACT_DIR / "brief.md").write_text(render_brief_markdown(brief), encoding="utf-8")
    print(brief["headline"])
    for action in brief["next_actions"]:
        check = action.get("check_result") or {}
        print(
            f"[{action['priority']}] {action['action']}  owner={action['owner']} "
            f"status={action['status']} check={check.get('status', 'unknown')}"
        )
    print(f"approvals: {len(approvals.requests)} ticket(s)")
    print(ARTIFACT_DIR / "brief.md")
    return 0


def cmd_approvals(args: argparse.Namespace) -> int:
    store = ApprovalStore(ARTIFACT_DIR / "approvals.json")
    if args.decide:
        try:
            ticket = store.decide(
                args.decide,
                args.decision,
                args.actor,
                args.reason,
            )
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{ticket['request_id']} -> {ticket['status']} by {ticket['decided_by']}")
        return 0
    summary = store.summary()
    print(f"Approval tickets: {summary['total']} {summary['by_status']}")
    for ticket in summary["requests"]:
        print(
            f"  {ticket['request_id']}  {ticket['status']:<8} "
            f"{ticket['target']}  approvers={','.join(ticket.get('approvers') or [])}"
        )
    return 0


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
    p.add_argument("--no-umodel", action="store_true", help="skip writing the code graph to UModel")
    p.set_defaults(func=cmd_code_demo)

    p = sub.add_parser("code-ingest", help="ingest real perf/bpftrace stacks into code RCA")
    _add_common(p)
    p.add_argument("--input", required=True, help="perf script or folded stack file")
    p.add_argument("--format", choices=["auto", "folded", "perf"], default="auto")
    p.add_argument("--case", default="case-01")
    p.add_argument("--repo", default="demo/fintech")
    p.set_defaults(func=cmd_code_ingest)

    p = sub.add_parser("real-perf-rea", help="real perf capture -> C code RCA -> 10-case REA")
    _add_common(p)
    p.add_argument("--iterations", type=int, default=10)
    p.set_defaults(func=cmd_real_perf)

    p = sub.add_parser("brief", help="write the on-call decision brief")
    _add_common(p)
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("approvals", help="list or decide approval tickets")
    _add_common(p)
    p.add_argument("--decide", default="", help="approval request id")
    p.add_argument("--decision", choices=["approved", "denied"], default="approved")
    p.add_argument("--actor", default="on-call")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_approvals)

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
