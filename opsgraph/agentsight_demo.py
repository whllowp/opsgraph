"""Deterministic AgentSight snapshot for the OpsGraph incident demo.

The shape follows ``agentsight report export`` schema version 1. It models two
runs around the payment incident:

* ``sre-claude-01`` investigates the incident, edits the checkout retry policy,
  and hits a permission/retry loop before applying it;
* ``codex-console-02`` improves the graph console and retries a failing test.

The data is synthetic but every field is one AgentSight actually emits, so the
same materializer works on a real ``report export`` file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SCENARIO_START
from .timeutil import iso
from .util import write_json

INCIDENT_ID = "INC-2026-1102-01"


def _ms(minute: float) -> int:
    return int((SCENARIO_START + minute * 60) * 1000)


def _session(
    session_id: str,
    agent: str,
    model: str,
    provider: str,
    start: float,
    end: float | None,
    pid: int,
    input_tokens: int,
    output_tokens: int,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": f"session-{session_id}",
        "agent_type": agent,
        "start_timestamp_ms": _ms(start),
        "end_timestamp_ms": _ms(end) if end else None,
        "status": "completed" if end else "running",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "view_source": "view",
        "confidence": 0.96,
        "attributes": {
            "session_id": session_id,
            "display_id": f"{agent}:{session_id}",
            "provider": provider,
            "pid": pid,
            **attributes,
        },
    }


def _tool(
    tool_id: str,
    session_id: str,
    minute: float,
    tool_name: str,
    status: str,
    payload: dict[str, Any],
    output: Any | None = None,
    duration_ms: int | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    return {
        "id": tool_id,
        "session_id": session_id,
        "timestamp_ms": _ms(minute),
        "tool_name": tool_name,
        "status": status,
        "duration_ms": duration_ms,
        "input": payload,
        "output": output,
        "related_pid": pid,
        "view_source": "view",
        "confidence": 0.94,
    }


def _audit(
    audit_id: str,
    minute: float,
    audit_type: str,
    action: str,
    target: str,
    status: str,
    summary: str,
    pid: int,
    comm: str,
    session_id: str,
    risk: str = "low",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = {"session_id": session_id, "risk": risk}
    if extra:
        details.update(extra)
    return {
        "id": audit_id,
        "timestamp_ms": _ms(minute),
        "audit_type": audit_type,
        "pid": pid,
        "comm": comm,
        "subject": session_id,
        "action": action,
        "target": target,
        "status": status,
        "summary": summary,
        "details": details,
        "view_source": "view",
        "confidence": 0.93,
    }


def demo_snapshot() -> dict[str, Any]:
    sre = "sre-claude-01"
    codex = "codex-console-02"

    sessions = [
        _session(
            sre,
            "claude",
            "claude-sonnet-4",
            "anthropic",
            44,
            58,
            6101,
            38200,
            6400,
            {
                "cwd": "/workspace/opsgraph",
                "prompt_preview": "Payment SLO is breaching. Use the UModel graph, find the root cause, and roll back the retry config.",
                "linked_service": "checkout-service",
                "incident_id": INCIDENT_ID,
                "claimed_root_cause": "checkout-service",
                "remediation_target": "checkout-service",
                "plan": [
                    {
                        "step": "Query UModel for the victim and upstream callers",
                        "status": "completed",
                    },
                    {
                        "step": "Correlate checkout retry change with eBPF amplification",
                        "status": "completed",
                    },
                    {"step": "Patch checkout retry policy", "status": "completed"},
                    {"step": "Apply to cluster and verify SLO", "status": "in_progress"},
                ],
            },
        ),
        _session(
            codex,
            "codex",
            "gpt-5",
            "openai",
            46,
            59,
            6201,
            21400,
            3400,
            {
                "cwd": "/workspace/opsgraph",
                "prompt_preview": "Add the AgentSight graph lane to the OpsGraph console and keep CI green.",
                "linked_service": "",
                "plan": [
                    {"step": "Inspect the console data model", "status": "completed"},
                    {"step": "Add agent tab and tests", "status": "in_progress"},
                ],
            },
        ),
    ]

    tool_calls = [
        _tool(
            "tool-sre-query",
            sre,
            44.5,
            "query_umodel",
            "success",
            {"query": ".entity with(domain='svc', name='svc.service', query='payment-gateway')"},
            {"root_cause": "checkout-service", "confidence": 0.78},
            1800,
            6101,
        ),
        _tool(
            "tool-sre-kubectl-get",
            sre,
            45.0,
            "kubectl_get",
            "success",
            {"command": "kubectl get pods -n payments -o wide"},
            {"pods": 3, "ready": 3},
            2400,
            6101,
        ),
        _tool(
            "tool-sre-read",
            sre,
            45.5,
            "read_file",
            "success",
            {"path": "/workspace/opsgraph/checkout-retry-policy.yaml"},
            {"lines": 18},
            120,
            6101,
        ),
        _tool(
            "tool-sre-patch-1",
            sre,
            46.0,
            "apply_patch",
            "failed",
            {
                "path": "/workspace/opsgraph/checkout-retry-policy.yaml",
                "patch": "max_retries: 5 -> 2",
            },
            {"error": "permission denied: config is read-only"},
            180,
            6101,
        ),
        _tool(
            "tool-sre-patch-2",
            sre,
            46.3,
            "apply_patch",
            "failed",
            {
                "path": "/workspace/opsgraph/checkout-retry-policy.yaml",
                "patch": "max_retries: 5 -> 2",
            },
            {"error": "permission denied: config is read-only"},
            160,
            6101,
        ),
        _tool(
            "tool-sre-patch-3",
            sre,
            47.0,
            "apply_patch",
            "success",
            {
                "path": "/workspace/opsgraph/checkout-retry-policy.yaml",
                "patch": "max_retries: 5 -> 2",
            },
            {"applied": True},
            220,
            6101,
        ),
        _tool(
            "tool-sre-apply-1",
            sre,
            48.0,
            "kubectl_apply",
            "failed",
            {"command": "kubectl apply -f checkout-retry-policy.yaml -n shop"},
            {"error": "forbidden: user cannot patch configmaps in prod"},
            3100,
            6110,
        ),
        _tool(
            "tool-sre-apply-2",
            sre,
            49.0,
            "kubectl_apply",
            "failed",
            {"command": "kubectl apply -f checkout-retry-policy.yaml -n shop"},
            {"error": "forbidden: user cannot patch configmaps in prod"},
            2900,
            6110,
        ),
        _tool(
            "tool-sre-apply-3",
            sre,
            50.0,
            "kubectl_apply",
            "success",
            {"command": "kubectl apply -f checkout-retry-policy.yaml -n shop"},
            {"configured": "checkout-retry-policy"},
            3400,
            6110,
        ),
        _tool(
            "tool-sre-rollout",
            sre,
            51.0,
            "kubectl_rollout",
            "success",
            {"command": "kubectl rollout restart deploy/checkout-service -n shop"},
            {"status": "restarted"},
            5200,
            6111,
        ),
        _tool(
            "tool-sre-verify",
            sre,
            54.0,
            "verify_slo",
            "success",
            {"service": "payment-gateway", "window": "5m"},
            {"p99_ms": 2210, "slo_ms": 800, "status": "still_breaching"},
            6400,
            6101,
        ),
        _tool(
            "tool-codex-read",
            codex,
            46.5,
            "read_file",
            "success",
            {"path": "/workspace/opsgraph/console/app.js"},
            {"lines": 920},
            140,
            6201,
        ),
        _tool(
            "tool-codex-search",
            codex,
            47.0,
            "search",
            "success",
            {"query": "build_console_state"},
            {"matches": 3},
            90,
            6201,
        ),
        _tool(
            "tool-codex-test-1",
            codex,
            48.0,
            "exec",
            "failed",
            {"command": "npm test -- --runInBand"},
            {"exit_code": 1, "stderr": "snapshot fixture mismatch"},
            8300,
            6202,
        ),
        _tool(
            "tool-codex-patch",
            codex,
            49.0,
            "apply_patch",
            "success",
            {"path": "/workspace/opsgraph/console/app.js"},
            {"applied": True},
            260,
            6201,
        ),
        _tool(
            "tool-codex-test-2",
            codex,
            51.0,
            "exec",
            "success",
            {"command": "npm test -- --runInBand"},
            {"exit_code": 0},
            6100,
            6203,
        ),
    ]

    audit_events = [
        _audit(
            "audit-sre-llm-1",
            44.1,
            "llm",
            "request",
            "api.anthropic.com",
            "success",
            "Investigate payment SLO breach using the UModel graph",
            6101,
            "claude",
            sre,
            extra={
                "text_content": "Payment P99 is 2.6s. Find the root cause and roll back the retry config."
            },
        ),
        _audit(
            "audit-sre-network-umodel",
            44.5,
            "network",
            "connect",
            "umodel.opsgraph.svc:18080",
            "success",
            "query UModel for payment-gateway",
            6101,
            "claude",
            sre,
            "medium",
        ),
        _audit(
            "audit-sre-file-read",
            45.5,
            "file",
            "read",
            "/workspace/opsgraph/checkout-retry-policy.yaml",
            "success",
            "read checkout retry policy",
            6101,
            "claude",
            sre,
            "low",
        ),
        _audit(
            "audit-sre-file-write",
            47.0,
            "file",
            "write",
            "/workspace/opsgraph/checkout-retry-policy.yaml",
            "success",
            "set max_retries 5 -> 2, timeout 2000ms -> 500ms",
            6101,
            "claude",
            sre,
            "high",
            {"diff": {"max_retries": [5, 2], "timeout_ms": [2000, 500]}},
        ),
        _audit(
            "audit-sre-kubectl-apply",
            50.0,
            "process",
            "exec",
            "kubectl apply -f checkout-retry-policy.yaml -n shop",
            "success",
            "applied checkout retry policy to prod cluster",
            6110,
            "kubectl",
            sre,
            "high",
        ),
        _audit(
            "audit-sre-kubectl-rollout",
            51.0,
            "process",
            "exec",
            "kubectl rollout restart deploy/checkout-service -n shop",
            "success",
            "restarted checkout-service deployment",
            6111,
            "kubectl",
            sre,
            "high",
        ),
        _audit(
            "audit-sre-network-k8s",
            50.0,
            "network",
            "connect",
            "kubernetes.default.svc:443",
            "success",
            "kubectl API call to prod cluster",
            6110,
            "kubectl",
            sre,
            "high",
        ),
        _audit(
            "audit-sre-verify",
            54.0,
            "network",
            "connect",
            "payments-api.svc:8080",
            "success",
            "verify payment SLO after rollout",
            6101,
            "claude",
            sre,
            "medium",
        ),
        _audit(
            "audit-codex-file-read",
            46.5,
            "file",
            "read",
            "/workspace/opsgraph/console/app.js",
            "success",
            "read console app",
            6201,
            "codex",
            codex,
            "low",
        ),
        _audit(
            "audit-codex-file-write",
            49.0,
            "file",
            "write",
            "/workspace/opsgraph/console/app.js",
            "success",
            "add agent lane to console",
            6201,
            "codex",
            codex,
            "low",
        ),
        _audit(
            "audit-codex-test-fail",
            48.0,
            "process",
            "exec",
            "npm test -- --runInBand",
            "failed",
            "snapshot fixture mismatch",
            6202,
            "node",
            codex,
            "medium",
        ),
        _audit(
            "audit-codex-test-pass",
            51.0,
            "process",
            "exec",
            "npm test -- --runInBand",
            "success",
            "all tests passed",
            6203,
            "node",
            codex,
            "low",
        ),
        _audit(
            "audit-codex-network-openai",
            47.0,
            "network",
            "connect",
            "api.openai.com:443",
            "success",
            "model completion",
            6201,
            "codex",
            codex,
            "low",
        ),
    ]

    processes = [
        {
            "id": "process-sre-root",
            "pid": 6101,
            "ppid": 1,
            "root_pid": 6101,
            "start_timestamp_ms": _ms(44),
            "end_timestamp_ms": _ms(58),
            "comm": "claude",
            "command": "claude --workspace /workspace/opsgraph",
            "argv": ["claude", "--workspace", "/workspace/opsgraph"],
            "cwd": "/workspace/opsgraph",
            "status": "success",
            "exit_code": 0,
            "view_source": "view",
        },
        {
            "id": "process-sre-kubectl",
            "pid": 6110,
            "ppid": 6101,
            "root_pid": 6101,
            "start_timestamp_ms": _ms(48),
            "end_timestamp_ms": _ms(51),
            "comm": "kubectl",
            "command": "kubectl apply -f checkout-retry-policy.yaml -n shop",
            "argv": ["kubectl", "apply", "-f", "checkout-retry-policy.yaml", "-n", "shop"],
            "cwd": "/workspace/opsgraph",
            "status": "success",
            "exit_code": 0,
            "view_source": "view",
        },
        {
            "id": "process-codex-root",
            "pid": 6201,
            "ppid": 1,
            "root_pid": 6201,
            "start_timestamp_ms": _ms(46),
            "end_timestamp_ms": _ms(59),
            "comm": "codex",
            "command": "codex --workspace /workspace/opsgraph",
            "argv": ["codex", "--workspace", "/workspace/opsgraph"],
            "cwd": "/workspace/opsgraph",
            "status": "running",
            "view_source": "view",
        },
        {
            "id": "process-codex-node",
            "pid": 6202,
            "ppid": 6201,
            "root_pid": 6201,
            "start_timestamp_ms": _ms(48),
            "end_timestamp_ms": _ms(51),
            "comm": "node",
            "command": "npm test -- --runInBand",
            "argv": ["npm", "test"],
            "cwd": "/workspace/opsgraph",
            "status": "success",
            "exit_code": 0,
            "view_source": "view",
        },
    ]

    network_targets = [
        {
            "pid": 6101,
            "comm": "claude",
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "count": 6,
            "error_count": 0,
            "first_timestamp_ms": _ms(44.1),
            "last_timestamp_ms": _ms(54),
        },
        {
            "pid": 6101,
            "comm": "claude",
            "host": "umodel.opsgraph.svc",
            "path": "/api/v1/query/opsgraph/execute",
            "count": 3,
            "error_count": 0,
            "first_timestamp_ms": _ms(44.5),
            "last_timestamp_ms": _ms(45.2),
        },
        {
            "pid": 6110,
            "comm": "kubectl",
            "host": "kubernetes.default.svc",
            "path": "/api/v1/namespaces/shop/configmaps",
            "count": 3,
            "error_count": 2,
            "first_timestamp_ms": _ms(48),
            "last_timestamp_ms": _ms(50),
        },
        {
            "pid": 6201,
            "comm": "codex",
            "host": "api.openai.com",
            "path": "/v1/responses",
            "count": 4,
            "error_count": 0,
            "first_timestamp_ms": _ms(46.2),
            "last_timestamp_ms": _ms(58),
        },
        {
            "pid": 6202,
            "comm": "node",
            "host": "registry.npmjs.org",
            "path": "/",
            "count": 2,
            "error_count": 0,
            "first_timestamp_ms": _ms(48),
            "last_timestamp_ms": _ms(51),
        },
    ]

    resource_samples = [
        {"timestamp_ms": _ms(45), "pid": 6101, "comm": "claude", "cpu_percent": 8.4, "rss_mb": 312},
        {
            "timestamp_ms": _ms(50),
            "pid": 6101,
            "comm": "claude",
            "cpu_percent": 21.7,
            "rss_mb": 428,
        },
        {
            "timestamp_ms": _ms(54),
            "pid": 6101,
            "comm": "claude",
            "cpu_percent": 12.1,
            "rss_mb": 446,
        },
        {"timestamp_ms": _ms(47), "pid": 6201, "comm": "codex", "cpu_percent": 6.3, "rss_mb": 218},
        {"timestamp_ms": _ms(51), "pid": 6201, "comm": "codex", "cpu_percent": 18.9, "rss_mb": 284},
    ]

    token_summary = [
        {
            "group": "anthropic / claude-sonnet-4",
            "input_tokens": 38200,
            "output_tokens": 6400,
            "cache_read_tokens": 12000,
            "total_tokens": 44600,
            "calls": 6,
            "sessions": 1,
        },
        {
            "group": "openai / gpt-5",
            "input_tokens": 21400,
            "output_tokens": 3400,
            "cache_read_tokens": 4200,
            "total_tokens": 24800,
            "calls": 4,
            "sessions": 1,
        },
    ]

    return {
        "schema_version": 1,
        "generated_at": "2026-11-02T01:00:00Z",
        "summary": {
            "source": "view",
            "view_events": len(audit_events) + len(tool_calls),
            "llm_calls": 10,
            "token_usage_rows": 10,
            "audit_events": len(audit_events),
            "sessions": len(sessions),
            "input_tokens": 59600,
            "output_tokens": 9800,
            "total_tokens": 69400,
            "start_timestamp_ms": _ms(44),
            "end_timestamp_ms": _ms(59),
            "audit_limit": 1000,
        },
        "token_summary": token_summary,
        "sessions": sessions,
        "tool_calls": tool_calls,
        "process_nodes": processes,
        "resource_samples": resource_samples,
        "network_targets": network_targets,
        "audit_events": audit_events,
    }


def generate_demo_snapshot(path: str | Path) -> Path:
    payload = demo_snapshot()
    target = Path(path)
    write_json(target, payload)
    return target


def write_readme_sample(path: str | Path) -> Path:
    """Write a tiny, redacted example used by the docs."""

    payload = demo_snapshot()
    for event in payload["audit_events"]:
        details = event.get("details")
        if isinstance(details, dict) and "text_content" in details:
            details["text_content"] = details["text_content"][:80]
    target = Path(path)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


__all__ = ["demo_snapshot", "generate_demo_snapshot", "write_readme_sample", "iso"]
