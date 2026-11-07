"""Persistent action ledger for the decision brief.

The brief is regenerated on every request, so a "closed" action would otherwise
forget that it was closed. This ledger appends only state transitions and
provides the audit chain: opened -> verification failed/blocked -> closed, or
reopened if the SLO regresses.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .verification import attach_checks

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _action_id(action: dict[str, Any]) -> str:
    raw = str(
        action.get("key")
        or "|".join(str(action.get(key) or "") for key in ("priority", "action", "owner"))
    )
    return "ACT-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


class ActionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def latest(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for event in self.events():
            action_id = str(event.get("action_id") or "")
            if action_id:
                latest[action_id] = event
        return latest

    def append(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def reconcile(
        self,
        brief: dict[str, Any],
        snapshot: dict[str, Any],
        approvals: list[dict[str, Any]] | None = None,
        code_rca: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actions = attach_checks(
            brief.get("next_actions") or [],
            snapshot,
            approvals=approvals,
            code_rca=code_rca,
        )
        with _LOCK:
            latest = self.latest()
            events: list[dict[str, Any]] = []
            for action in actions:
                action_id = _action_id(action)
                action["action_id"] = action_id
                previous = latest.get(action_id)
                current = str(action.get("status") or "open")
                if previous is None:
                    events.append(self._event(action, "opened", current))
                    if current == "closed":
                        events.append(self._event(action, "closed", current))
                elif previous.get("status") != current:
                    event = "reopened" if current == "open" else current
                    if current == "closed":
                        event = "closed"
                    events.append(self._event(action, event, current))
            self.append(events)
            all_events = self.events()[-40:]
        closed = sum(1 for item in actions if item.get("status") == "closed")
        p0 = [item for item in actions if item.get("priority") == "P0"]
        if p0 and all(item.get("status") == "closed" for item in p0):
            brief["status"] = "verified"
            brief["resolution"] = "P0 executable checks passed; action closed by the ledger"
            brief["novice"]["now"] = "SLO 检查已通过，P0 动作已自动关闭；继续观察一个窗口。"
        brief["next_actions"] = actions
        brief["audit"] = {
            "action_count": len(actions),
            "closed": closed,
            "open": len(actions) - closed,
            "transition_count": len(events),
            "ledger": str(self.path),
        }
        brief["history"] = all_events
        return brief

    @staticmethod
    def _event(action: dict[str, Any], event: str, status: str) -> dict[str, Any]:
        return {
            "ts": _now(),
            "event": event,
            "action_id": action.get("action_id"),
            "priority": action.get("priority"),
            "action": action.get("action"),
            "owner": action.get("owner"),
            "status": status,
            "check_result": action.get("check_result") or {},
        }
