"""AgentSight data adapters.

AgentSight is the eBPF sensor for AI-agent behaviour: it records LLM calls,
tool decisions, process/file/network effects, resource samples, and token
usage. This module normalizes its two public artifacts into one model:

* ``agentsight report export -o snapshot.json`` (schema version 1);
* a recorded ``agentsight-*.db`` SQLite file.

Only public, documented fields are used. Captured content is treated as
sensitive: the graph builder stores short summaries and derived risk, not raw
prompt or response bodies.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_SCHEMA = 1


def _ms(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value)[:limit]


@dataclass
class AgentSession:
    id: str
    key: str
    agent_type: str
    model: str
    provider: str
    start_ms: int | None
    end_ms: int | None
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    attributes: dict[str, Any] = field(default_factory=dict)
    pid: int | None = None
    view_source: str = "unknown"
    confidence: float | None = None
    cpu_peak: float = 0.0
    rss_peak_mb: int = 0

    @property
    def duration_s(self) -> float:
        if not self.start_ms:
            return 0.0
        end = self.end_ms or self.start_ms
        return max(0.0, (end - self.start_ms) / 1000.0)

    @property
    def display_name(self) -> str:
        return str(
            self.attributes.get("display_id") or self.attributes.get("session_id") or self.id
        )


@dataclass
class ToolCall:
    id: str
    session_key: str
    timestamp_ms: int
    tool_name: str
    status: str
    duration_ms: int | None
    input: Any
    output: Any
    target: str
    related_pid: int | None
    view_source: str = "unknown"
    confidence: float | None = None

    @property
    def failed(self) -> bool:
        return self.status.lower() in {"failed", "error", "denied", "timeout", "cancelled"}


@dataclass
class Effect:
    id: str
    timestamp_ms: int
    effect_type: str
    action: str
    target: str
    status: str
    summary: str
    details: Any = field(default_factory=dict)
    pid: int | None = None
    comm: str = ""
    confidence: float | None = None
    view_source: str = "unknown"
    session_key: str | None = None


@dataclass
class ModelUsage:
    group: str
    provider: str
    model: str
    calls: int
    sessions: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int = 0


@dataclass
class AgentSnapshot:
    schema_version: int
    generated_at: str
    summary: dict[str, Any]
    sessions: list[AgentSession]
    tool_calls: list[ToolCall]
    effects: list[Effect]
    models: list[ModelUsage]
    source_path: str
    source_kind: str
    raw: dict[str, Any] = field(default_factory=dict)

    def session_aliases(self) -> dict[str, AgentSession]:
        aliases: dict[str, AgentSession] = {}
        for session in self.sessions:
            for key in (
                session.id,
                session.key,
                str(session.attributes.get("session_id") or ""),
                str(session.attributes.get("display_id") or ""),
                f"pid:{session.pid}" if session.pid else "",
            ):
                if key:
                    aliases[key] = session
        return aliases


def _session_from_row(row: dict[str, Any]) -> AgentSession:
    attributes = _json(row.get("attributes")) or {}
    if not isinstance(attributes, dict):
        attributes = {"value": attributes}
    pid = _ms(attributes.get("pid") or attributes.get("process_pid"))
    session_id = str(row.get("id") or "")
    key = str(attributes.get("session_id") or session_id)
    model = str(row.get("model") or "unknown")
    provider = str(attributes.get("provider") or model.split("/")[0].split("-")[0])
    return AgentSession(
        id=session_id,
        key=key,
        agent_type=str(row.get("agent_type") or "unknown"),
        model=model,
        provider=provider,
        start_ms=_ms(row.get("start_timestamp_ms")),
        end_ms=_ms(row.get("end_timestamp_ms")),
        status=str(row.get("status") or "unknown"),
        input_tokens=_int(row.get("input_tokens")),
        output_tokens=_int(row.get("output_tokens")),
        total_tokens=_int(row.get("total_tokens")),
        attributes=attributes,
        pid=pid,
        view_source=str(row.get("view_source") or "unknown"),
        confidence=row.get("confidence"),
    )


def _tool_target(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("path", "file", "file_path", "target", "command", "cmd", "query", "url"):
            if payload.get(key):
                return _text(payload.get(key), 160)
        if payload:
            first = next(iter(payload.values()))
            return _text(first, 160)
    if payload not in (None, ""):
        return _text(payload, 160)
    return ""


def _tool_from_row(row: dict[str, Any]) -> ToolCall:
    input_payload = _json(row.get("input_json") if "input_json" in row else row.get("input"))
    output_payload = _json(row.get("output_json") if "output_json" in row else row.get("output"))
    if input_payload is None and "tool_input" in row:
        input_payload = _json(row.get("tool_input"))
    target = _tool_target(input_payload)
    if not target:
        target = _tool_target(output_payload)
    return ToolCall(
        id=str(row.get("id") or ""),
        session_key=str(row.get("session_id") or ""),
        timestamp_ms=_ms(row.get("timestamp_ms")) or 0,
        tool_name=str(row.get("tool_name") or "tool"),
        status=str(row.get("status") or "observed"),
        duration_ms=_ms(row.get("duration_ms")),
        input=input_payload,
        output=output_payload,
        target=target,
        related_pid=_ms(row.get("related_pid")),
        view_source=str(row.get("view_source") or "unknown"),
        confidence=row.get("confidence"),
    )


def _effect_from_audit(row: dict[str, Any]) -> Effect:
    details = _json(row.get("details_json") if "details_json" in row else row.get("details"))
    details = details or {}
    session_key = None
    if isinstance(details, dict):
        session_key = details.get("session_id") or details.get("session_key")
    return Effect(
        id=str(row.get("id") or ""),
        timestamp_ms=_ms(row.get("timestamp_ms")) or 0,
        effect_type=str(row.get("audit_type") or "unknown"),
        action=str(row.get("action") or ""),
        target=str(row.get("target") or ""),
        status=str(row.get("status") or "observed"),
        summary=str(row.get("summary") or ""),
        details=details,
        pid=_ms(row.get("pid")),
        comm=str(row.get("comm") or ""),
        confidence=row.get("confidence"),
        view_source=str(row.get("view_source") or "unknown"),
        session_key=str(session_key) if session_key else None,
    )


def _model_usages(rows: Iterable[dict[str, Any]]) -> list[ModelUsage]:
    result = []
    for row in rows:
        group = str(row.get("group") or row.get("model") or "unknown")
        provider, model = _split_group(group)
        result.append(
            ModelUsage(
                group=group,
                provider=provider,
                model=model,
                calls=_int(row.get("calls")),
                sessions=_int(row.get("sessions")),
                input_tokens=_int(row.get("input_tokens")),
                output_tokens=_int(row.get("output_tokens")),
                total_tokens=_int(row.get("total_tokens")),
                cache_read_tokens=_int(row.get("cache_read_tokens")),
            )
        )
    return result


def _split_group(group: str) -> tuple[str, str]:
    if "/" in group:
        left, right = group.split("/", 1)
        return left.strip(), right.strip()
    return "", group.strip()


def _resource_peaks(snapshot: dict[str, Any], sessions: list[AgentSession]) -> None:
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for row in snapshot.get("resource_samples") or []:
        pid = _ms(row.get("pid"))
        if pid:
            by_pid.setdefault(pid, []).append(row)
    for session in sessions:
        if not session.pid:
            continue
        rows = by_pid.get(session.pid, [])
        if not rows:
            continue
        session.cpu_peak = max(_float(row.get("cpu_percent")) for row in rows)
        session.rss_peak_mb = max(_int(row.get("rss_mb")) for row in rows)


def load_snapshot(path: str | Path) -> AgentSnapshot:
    target = Path(path)
    with target.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    version = _int(raw.get("schema_version"), 0)
    if version != SUPPORTED_SCHEMA:
        raise ValueError(f"unsupported AgentSight snapshot schema_version={version}")

    sessions = [_session_from_row(row) for row in raw.get("sessions") or []]
    tool_calls = [_tool_from_row(row) for row in raw.get("tool_calls") or []]
    aliases = {session.key: session for session in sessions}
    for session in sessions:
        aliases.setdefault(str(session.attributes.get("session_id") or ""), session)
    for tool in tool_calls:
        session = aliases.get(tool.session_key)
        if session and not session.pid and tool.related_pid:
            session.pid = tool.related_pid
    _resource_peaks(raw, sessions)
    effects = [_effect_from_audit(row) for row in raw.get("audit_events") or []]
    models = _model_usages(raw.get("token_summary") or [])
    return AgentSnapshot(
        schema_version=version,
        generated_at=str(raw.get("generated_at") or ""),
        summary=raw.get("summary") or {},
        sessions=sessions,
        tool_calls=tool_calls,
        effects=effects,
        models=models,
        source_path=str(target),
        source_kind="snapshot",
        raw=raw,
    )


# --------------------------------------------------------------------------- SQLite


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _fetch(conn: sqlite3.Connection, table: str, names: set[str]) -> list[dict[str, Any]]:
    if table not in names:
        return []
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def load_sqlite(path: str | Path) -> AgentSnapshot:
    target = Path(path)
    conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        names = _table_names(conn)
        llm_calls = _fetch(conn, "llm_calls", names)
        token_rows = _fetch(conn, "token_usage", names)
        tool_rows = _fetch(conn, "tool_calls", names)
        audit_rows = _fetch(conn, "audit_events", names)
        process_rows = _fetch(conn, "process_nodes", names)
        network_rows = _fetch(conn, "network_targets", names)
        resource_rows = _fetch(conn, "resource_samples", names)
    finally:
        conn.close()

    tokens_by_call: dict[str, list[dict[str, Any]]] = {}
    for row in token_rows:
        call_id = str(row.get("llm_call_id") or "")
        if call_id:
            tokens_by_call.setdefault(call_id, []).append(row)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in llm_calls:
        key = str(row.get("session_id") or f"pid:{row.get('pid')}")
        grouped.setdefault(key, []).append(row)

    sessions: list[AgentSession] = []
    for key, rows in grouped.items():
        start = min((_ms(row.get("start_timestamp_ms")) or 0) for row in rows)
        ends = [(_ms(row.get("end_timestamp_ms")) or 0) for row in rows]
        end = max(ends) if ends else None
        models = [str(row.get("model") or "") for row in rows if row.get("model")]
        provider = next((str(row.get("provider")) for row in rows if row.get("provider")), "")
        pid = next((_ms(row.get("pid")) for row in rows if row.get("pid")), None)
        input_tokens = output_tokens = total_tokens = 0
        for row in rows:
            for token in tokens_by_call.get(str(row.get("id")), []):
                input_tokens += _int(token.get("input_tokens"))
                output_tokens += _int(token.get("output_tokens"))
                total_tokens += _int(token.get("total_tokens"))
        attributes: dict[str, Any] = {"session_id": key}
        process = next(
            (
                item
                for item in process_rows
                if item.get("pid") == pid or item.get("root_pid") == pid
            ),
            None,
        )
        if process:
            attributes["cwd"] = process.get("cwd")
            attributes["command"] = process.get("command")
        sessions.append(
            AgentSession(
                id=f"session-{key}",
                key=key,
                agent_type=str(rows[0].get("comm") or "agent"),
                model=models[0] if models else "unknown",
                provider=provider,
                start_ms=start or None,
                end_ms=end,
                status="observed",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                attributes=attributes,
                pid=pid,
                view_source=str(rows[0].get("view_source") or "sqlite"),
            )
        )

    aliases = {session.key: session for session in sessions}
    _resource_peaks({"resource_samples": resource_rows}, sessions)
    tool_calls = []
    for row in tool_rows:
        item = _tool_from_row(row)
        if item.session_key in aliases:
            tool_calls.append(item)

    effects = [_effect_from_audit(row) for row in audit_rows]
    for row in network_rows:
        effects.append(
            Effect(
                id=f"network:{row.get('host')}:{row.get('path') or ''}",
                timestamp_ms=_ms(row.get("first_timestamp_ms")) or 0,
                effect_type="network",
                action="connect",
                target=f"{row.get('host')}:443" if row.get("host") else "",
                status="success" if not _int(row.get("error_count")) else "error",
                summary=f"{row.get('count', 0)} requests to {row.get('host')}",
                details=row,
                pid=_ms(row.get("pid")),
                comm=str(row.get("comm") or ""),
                view_source="sqlite",
            )
        )

    models_by_group: dict[str, ModelUsage] = {}
    for row in token_rows:
        group = str(row.get("model") or "unknown")
        provider = str(row.get("provider") or "")
        usage = models_by_group.setdefault(
            group,
            ModelUsage(
                group=group,
                provider=provider,
                model=group,
                calls=0,
                sessions=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            ),
        )
        usage.calls += 1
        usage.input_tokens += _int(row.get("input_tokens"))
        usage.output_tokens += _int(row.get("output_tokens"))
        usage.total_tokens += _int(row.get("total_tokens"))

    return AgentSnapshot(
        schema_version=SUPPORTED_SCHEMA,
        generated_at="",
        summary={
            "source": "sqlite",
            "sessions": len(sessions),
            "llm_calls": len(llm_calls),
            "input_tokens": sum(item.input_tokens for item in sessions),
            "output_tokens": sum(item.output_tokens for item in sessions),
            "total_tokens": sum(item.total_tokens for item in sessions),
            "audit_events": len(effects),
        },
        sessions=sessions,
        tool_calls=tool_calls,
        effects=effects,
        models=list(models_by_group.values()),
        source_path=str(target),
        source_kind="sqlite",
        raw={"resource_samples": resource_rows},
    )


def load_agentsight(path: str | Path | None) -> AgentSnapshot | None:
    if path is None:
        return None
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"AgentSight artifact not found: {target}")
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return load_sqlite(target)
    return load_snapshot(target)
