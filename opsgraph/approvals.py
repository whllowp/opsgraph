"""Approval tickets for brief actions and policy ``require_approval``.

``require_approval`` used to be a sentence in the brief. This module turns it
into a durable ticket that can be listed, approved/denied and checked by the
action ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import write_json


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ticket_id(target: str, rule: str) -> str:
    digest = hashlib.sha1(f"{target}|{rule}".encode("utf-8")).hexdigest()[:10]
    return f"APR-{digest}"


class ApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.requests: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.requests = list(payload.get("requests") or [])

    def save(self) -> None:
        write_json(
            self.path,
            {
                "schema_version": 1,
                "generated_at": _now(),
                "requests": self.requests,
            },
        )

    def ensure_from_policy(self, policy: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = {str(item.get("request_id")): item for item in self.requests}
        created = []
        for decision in policy:
            if decision.get("decision") != "require_approval":
                continue
            target = str(decision.get("target") or "")
            rule = str(decision.get("rule") or "")
            request_id = _ticket_id(target, rule)
            if request_id in existing:
                continue
            ticket = {
                "request_id": request_id,
                "status": "pending",
                "target": target,
                "rule": rule,
                "service": decision.get("service") or "",
                "approvers": list(decision.get("approvers") or ["change-manager"]),
                "reasons": list(decision.get("reasons") or []),
                "created_at": _now(),
                "decided_at": "",
                "decided_by": "",
                "reason": "",
            }
            self.requests.append(ticket)
            existing[request_id] = ticket
            created.append(ticket)
        if created:
            self.save()
        return created

    def for_target(self, target: str) -> dict[str, Any]:
        return next(
            (item for item in self.requests if item.get("target") == target),
            {},
        )

    def decide(
        self,
        request_id: str,
        decision: str,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approved", "denied"}:
            raise ValueError("decision must be approved or denied")
        ticket = next(
            (item for item in self.requests if item.get("request_id") == request_id),
            None,
        )
        if not ticket:
            raise KeyError(f"approval not found: {request_id}")
        if ticket.get("status") != "pending":
            raise ValueError(f"approval already {ticket.get('status')}")
        ticket.update(
            {
                "status": decision,
                "decided_at": _now(),
                "decided_by": actor,
                "reason": reason,
            }
        )
        self.save()
        return ticket

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for ticket in self.requests:
            status = str(ticket.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
        return {"total": len(self.requests), "by_status": counts, "requests": self.requests}
