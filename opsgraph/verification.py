"""Executable checks for brief actions.

The brief used to contain prose such as "P99 returns to baseline". This module
turns that prose into a machine-evaluable check attached to an action, so the
action can close only when the SLO actually recovers.

Supported checks:

* ``slo``: current service metric vs target, from the materialized snapshot
* ``approval``: approval ticket status
* ``code_regression``: final code regression from the REA artifact
* ``manual``: no automatic close; a human must decide
"""

from __future__ import annotations

from typing import Any


def _service(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in snapshot.get("entities", [])
            if item.get("__entity_type__") == "svc.service" and item.get("display_name") == name
        ),
        {},
    )


def _result(
    kind: str,
    status: str,
    *,
    message: str = "",
    actual: float | None = None,
    target: float | None = None,
    observed_at: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": status,
        "message": message,
        "actual": actual,
        "target": target,
        "observed_at": observed_at,
        "evidence": evidence or {},
    }


def evaluate_check(
    check: dict[str, Any] | None,
    snapshot: dict[str, Any],
    approvals: list[dict[str, Any]] | None = None,
    code_rca: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not check:
        return _result("manual", "unknown", message="no executable check attached")
    kind = str(check.get("kind") or "manual")
    observed_at = str((snapshot.get("window") or {}).get("end_iso") or "")
    if kind == "slo":
        service = _service(snapshot, str(check.get("service") or ""))
        if not service:
            return _result(kind, "unknown", message="service not found in snapshot")
        metric = str(check.get("metric") or "latency_p99_ms")
        actual = float(service.get(metric) or 0)
        target = float(check.get("target") or 0)
        operator = str(check.get("operator") or "<=")
        passed = actual <= target if operator == "<=" else actual >= target
        return _result(
            kind,
            "passed" if passed else "failed",
            message=(
                f"{service.get('display_name')} {metric}={actual:.1f} {operator} {target:.1f}"
            ),
            actual=actual,
            target=target,
            observed_at=observed_at,
            evidence={"service": service.get("display_name"), "metric": metric},
        )
    if kind == "approval":
        request_id = str(check.get("request_id") or "")
        ticket = next(
            (item for item in approvals or [] if item.get("request_id") == request_id),
            None,
        )
        if not ticket:
            return _result(kind, "pending", message=f"approval {request_id} not created")
        status = str(ticket.get("status") or "pending")
        return _result(
            kind,
            "passed" if status == "approved" else "failed" if status == "denied" else "pending",
            message=f"approval {request_id} is {status}",
            evidence={"target": ticket.get("target"), "approvers": ticket.get("approvers")},
        )
    if kind == "code_regression":
        summary = (code_rca or {}).get("summary") or {}
        final = (code_rca or {}).get("final_suite") or summary.get("final_suite") or {}
        passed = bool(final.get("passed"))
        return _result(
            kind,
            "passed" if passed else "failed",
            message="code regression passed" if passed else "code regression failed",
            evidence={"fixed": summary.get("fixed")},
        )
    if kind == "manual":
        return _result(kind, "unknown", message="manual verification required")
    return _result(kind, "unknown", message=f"unknown check kind: {kind}")


def action_status(result: dict[str, Any], check: dict[str, Any] | None = None) -> str:
    status = str(result.get("status") or "unknown")
    if status == "passed":
        if str((check or {}).get("kind")) == "approval":
            return "unblocked"
        return "closed"
    if status == "pending":
        return "blocked"
    if status == "failed":
        return "open"
    return "open"


def attach_checks(
    actions: list[dict[str, Any]],
    snapshot: dict[str, Any],
    approvals: list[dict[str, Any]] | None = None,
    code_rca: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    for action in actions:
        result = evaluate_check(
            action.get("check"),
            snapshot,
            approvals=approvals,
            code_rca=code_rca,
        )
        action["check_result"] = result
        action["status"] = action_status(result, action.get("check"))
    return actions
