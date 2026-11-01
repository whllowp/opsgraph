"""Compile AgentSight observations into UModel graph entities.

This is the AI-era extension of OpsGraph's graph engineering:

* an agent session is a first-class node, not a log line;
* a tool call is a decision node, and an effect is the system evidence it
  produced;
* token/cost, retry loops, risk, and graph-grounded claims are attached to the
  session, so the same explainable analysis used for services applies to agents.

The builder is deterministic and does not call an LLM.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import ids
from .agentsight import AgentSession, AgentSnapshot, Effect, ToolCall
from .config import SCENARIO_START
from .timeutil import clock

RISK_WEIGHT = {"high": 30, "medium": 5, "low": 1}
HIGH_RISK_TOOLS = {
    "apply_patch",
    "write_file",
    "edit_file",
    "kubectl_apply",
    "kubectl_delete",
    "kubectl_rollout",
    "terraform_apply",
    "helm_upgrade",
    "exec",
}
HIGH_RISK_COMMAND_TOKENS = (
    "kubectl apply",
    "kubectl delete",
    "kubectl rollout",
    "kubectl patch",
    "rm -rf",
    "terraform apply",
    "helm upgrade",
    "drop table",
    "truncate ",
    "chmod 777",
)
HIGH_RISK_PATH_TOKENS = ("/prod", "production", "secrets", ".env", "retry-policy", "configmap")
MODEL_PRICES = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.25, 1.25),
    "gpt-5": (1.25, 10.0),
    "gpt-4": (2.5, 10.0),
    "gemini": (1.25, 10.0),
    "qwen": (0.4, 1.2),
}


def _ms_to_s(value: int | None) -> int:
    return int((value or 0) / 1000)


def _price(model: str) -> tuple[float, float]:
    key = model.lower()
    for token, price in MODEL_PRICES.items():
        if token in key:
            return price
    return (1.0, 3.0)


def model_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = _price(model)
    return round(
        input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price, 4
    )


def _effect_risk(effect: Effect) -> str:
    details = effect.details if isinstance(effect.details, dict) else {}
    explicit = str(details.get("risk") or "").lower()
    if explicit in RISK_WEIGHT:
        return explicit
    target = effect.target.lower()
    action = effect.action.lower()
    if any(token in target for token in HIGH_RISK_COMMAND_TOKENS):
        return "high"
    if any(token in target for token in HIGH_RISK_PATH_TOKENS) and action in {
        "write",
        "delete",
        "patch",
        "exec",
    }:
        return "high"
    if effect.effect_type == "network" and any(
        token in target for token in ("kubernetes", "prod", "admin")
    ):
        return "high"
    if effect.effect_type == "process" and action in {"exec", "spawn"}:
        if any(token in target for token in HIGH_RISK_COMMAND_TOKENS):
            return "high"
        return "medium"
    if effect.status.lower() in {"failed", "error", "denied"}:
        return "medium"
    if effect.effect_type == "file" and action in {"write", "delete", "patch"}:
        return "medium"
    return "low"


def _tool_risk(tool: ToolCall) -> str:
    name = tool.tool_name.lower()
    target = tool.target.lower()
    command = ""
    if isinstance(tool.input, dict):
        command = str(tool.input.get("command") or "").lower()
    if name in HIGH_RISK_TOOLS:
        if any(token in target for token in HIGH_RISK_COMMAND_TOKENS) or any(
            token in command for token in HIGH_RISK_COMMAND_TOKENS
        ):
            return "high"
        if name.startswith("kubectl") or name in {
            "apply_patch",
            "write_file",
            "edit_file",
            "terraform_apply",
        }:
            return "high"
        return "medium"
    if any(token in target for token in HIGH_RISK_COMMAND_TOKENS):
        return "high"
    if tool.failed:
        return "medium"
    return "low"


def _command_of(tool: ToolCall) -> str:
    if isinstance(tool.input, dict):
        return str(tool.input.get("command") or tool.input.get("cmd") or "")
    return ""


def _display_session(session: AgentSession) -> str:
    return str(session.attributes.get("display_id") or session.key)


def _session_model_key(session: AgentSession) -> str:
    return session.model or "unknown"


def _tool_intent_bonus(tool_name: str, effect: Effect) -> int:
    name = tool_name.lower()
    etype = effect.effect_type.lower()
    action = effect.action.lower()
    if "read" in name and etype == "file" and action == "read":
        return 3
    if (
        any(token in name for token in ("apply_patch", "write", "edit"))
        and etype == "file"
        and action in {"write", "patch", "delete"}
    ):
        return 3
    if any(token in name for token in ("kubectl", "exec", "shell", "bash")) and etype == "process":
        return 3
    if (
        any(token in name for token in ("query", "verify", "http", "fetch", "curl"))
        and etype == "network"
    ):
        return 2
    return 0


class AgentGraphBuilder:
    def __init__(
        self,
        snapshot: dict[str, Any],
        agent_snapshot: AgentSnapshot,
        service_analysis: dict[str, Any],
    ) -> None:
        self.snapshot = snapshot
        self.agent_snapshot = agent_snapshot
        self.service_analysis = service_analysis
        self.entities: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.aliases = agent_snapshot.session_aliases()
        self.sessions = {session.key: session for session in agent_snapshot.sessions}
        self.services = {
            str(item.get("display_name")): item
            for item in snapshot.get("entities", [])
            if item.get("__entity_type__") == "svc.service"
        }
        self.incident = next(
            (
                item
                for item in snapshot.get("entities", [])
                if item.get("__entity_type__") == "ops.incident"
            ),
            {},
        )
        self.tools_by_session: dict[str, list[ToolCall]] = defaultdict(list)
        for tool in agent_snapshot.tool_calls:
            session = self.aliases.get(tool.session_key)
            if session:
                tool.session_key = session.key
                self.tools_by_session[session.key].append(tool)
        self._pid_to_session = self._build_pid_map()
        self._tool_retry: dict[str, tuple[int, str]] = {}
        self._tool_entities: dict[str, dict[str, Any]] = {}
        self._effect_entities: dict[str, dict[str, Any]] = {}
        self._effect_ids: dict[str, str] = {}
        self._effect_session: dict[str, str] = {}
        self._effect_tool: dict[str, str] = {}
        self.incident_eid = str(self.incident.get("__entity_id__") or "")

    def _build_pid_map(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for session in self.sessions.values():
            if session.pid:
                mapping[session.pid] = session.key
        for row in self.agent_snapshot.raw.get("process_nodes") or []:
            root = row.get("root_pid") or row.get("pid")
            pid = row.get("pid")
            if not root or not pid:
                continue
            for session in self.sessions.values():
                if session.pid == root:
                    mapping[int(pid)] = session.key
        return mapping

    def build(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        self._build_models()
        self._build_sessions()
        self._build_tools()
        self._build_effects()
        self._link_services()
        analysis = self._analyze()
        return self.entities, self.relations, analysis

    # ------------------------------------------------------------------ entities
    def _build_models(self) -> None:
        usage = list(self.agent_snapshot.models)
        by_model: dict[str, dict[str, Any]] = {}
        for row in usage:
            key = row.model or row.group
            item = by_model.setdefault(
                key,
                {
                    "provider": row.provider,
                    "model": row.model or key,
                    "calls": 0,
                    "sessions": 0,
                    "input": 0,
                    "output": 0,
                    "total": 0,
                },
            )
            item["calls"] += row.calls
            item["sessions"] += row.sessions
            item["input"] += row.input_tokens
            item["output"] += row.output_tokens
            item["total"] += row.total_tokens
        if not by_model:
            for session in self.sessions.values():
                key = _session_model_key(session)
                item = by_model.setdefault(
                    key,
                    {
                        "provider": session.provider,
                        "model": session.model,
                        "calls": 0,
                        "sessions": 0,
                        "input": 0,
                        "output": 0,
                        "total": 0,
                    },
                )
                item["sessions"] += 1
                item["input"] += session.input_tokens
                item["output"] += session.output_tokens
                item["total"] += session.total_tokens
        for key, item in by_model.items():
            eid = ids.model_id(key)
            self.entities.append(
                _entity(
                    "ai.model",
                    eid,
                    key,
                    {
                        "provider": item["provider"],
                        "model": item["model"],
                        "calls": item["calls"],
                        "sessions": item["sessions"],
                        "input_tokens": item["input"],
                        "output_tokens": item["output"],
                        "total_tokens": item["total"],
                        "cost_usd": model_cost(key, item["input"], item["output"]),
                    },
                    _ms_to_s(self.agent_snapshot.sessions[0].start_ms)
                    if self.agent_snapshot.sessions
                    else SCENARIO_START,
                    _ms_to_s(self.agent_snapshot.sessions[0].end_ms)
                    if self.agent_snapshot.sessions
                    else SCENARIO_START,
                )
            )

    def _build_sessions(self) -> None:
        model_ids = {
            str(item.get("display_name")): str(item.get("__entity_id__"))
            for item in self.entities
            if item.get("__entity_type__") == "ai.model"
        }
        root_cause = str(self.service_analysis.get("root_cause") or "")
        for session in self.agent_snapshot.sessions:
            tools = self.tools_by_session.get(session.key, [])
            effects = self._effects_for_session(session.key)
            failures = sum(1 for tool in tools if tool.failed)
            loops = self._loops_for(session.key, tools)
            file_reads = sum(
                1 for effect in effects if effect.effect_type == "file" and effect.action == "read"
            )
            file_writes = sum(
                1
                for effect in effects
                if effect.effect_type == "file" and effect.action in {"write", "delete", "patch"}
            )
            network = sum(1 for effect in effects if effect.effect_type == "network")
            commands = sum(1 for effect in effects if effect.effect_type == "process")
            risk_score = sum(RISK_WEIGHT[_effect_risk(effect)] for effect in effects)
            risk_score += failures * 4 + len(loops) * 8
            claim = str(session.attributes.get("claimed_root_cause") or "")
            claim_status = "unverifiable"
            if claim and root_cause:
                claim_status = "verified" if claim == root_cause else "mismatch"
            if claim_status == "mismatch":
                risk_score += 15
            risk_score = round(min(100.0, risk_score), 1)
            risk_level = "high" if risk_score >= 60 else "medium" if risk_score >= 30 else "low"
            friction = min(
                100.0,
                failures * 10
                + len(loops) * 15
                + max(0.0, session.duration_s - 600) / 60 * 2
                + session.total_tokens / 10000,
            )
            remediation_target = str(session.attributes.get("remediation_target") or "")
            remediation_status = "none"
            if remediation_target and remediation_target in self.services:
                remediation_status = (
                    "pending_validation"
                    if str(self.services[remediation_target].get("status"))
                    in {"critical", "warning"}
                    else "applied"
                )
            linked_service = str(session.attributes.get("linked_service") or "")
            incident_id = str(session.attributes.get("incident_id") or "")
            cost = model_cost(session.model, session.input_tokens, session.output_tokens)
            eid = ids.agent_session_id(session.key)
            self.entities.append(
                _entity(
                    "ai.agent_session",
                    eid,
                    _display_session(session),
                    {
                        "session_key": session.key,
                        "agent_type": session.agent_type,
                        "model": session.model,
                        "provider": session.provider,
                        "status": session.status,
                        "start_ts": _ms_to_s(session.start_ms),
                        "end_ts": _ms_to_s(session.end_ms),
                        "duration_s": round(session.duration_s, 1),
                        "input_tokens": session.input_tokens,
                        "output_tokens": session.output_tokens,
                        "total_tokens": session.total_tokens,
                        "cost_usd": cost,
                        "tool_calls": len(tools),
                        "failed_tool_calls": failures,
                        "retry_loops": len(loops),
                        "files_read": file_reads,
                        "files_written": file_writes,
                        "network_calls": network,
                        "commands": commands,
                        "risk_score": risk_score,
                        "friction_score": round(friction, 1),
                        "risk_level": risk_level,
                        "cwd": str(session.attributes.get("cwd") or ""),
                        "prompt_preview": str(session.attributes.get("prompt_preview") or "")[:240],
                        "linked_service": linked_service,
                        "incident_id": incident_id,
                        "claim_root_cause": claim,
                        "claim_status": claim_status,
                        "remediation_status": remediation_status,
                        "source": f"agentsight.{self.agent_snapshot.source_kind}",
                    },
                    _ms_to_s(session.start_ms),
                    _ms_to_s(session.end_ms) or _ms_to_s(session.start_ms),
                )
            )
            model_key = _session_model_key(session)
            model_eid = model_ids.get(model_key)
            if model_eid:
                self.relations.append(
                    _relation(
                        "ai.agent_session",
                        eid,
                        "ai.model",
                        model_eid,
                        "uses_model",
                        f"{_display_session(session)} uses {model_key}",
                        _ms_to_s(session.start_ms),
                        _ms_to_s(session.end_ms),
                        {"tokens": session.total_tokens, "cost_usd": cost},
                    )
                )
            if linked_service in self.services:
                self.relations.append(
                    _relation(
                        "ai.agent_session",
                        eid,
                        "svc.service",
                        ids.service_id(linked_service),
                        "acts_on",
                        f"{_display_session(session)} acts on {linked_service}",
                        _ms_to_s(session.start_ms),
                        _ms_to_s(session.end_ms),
                    )
                )
            if incident_id and self.incident:
                self.relations.append(
                    _relation(
                        "ai.agent_session",
                        eid,
                        "ops.incident",
                        self.incident_eid,
                        "investigates",
                        f"{_display_session(session)} investigates {incident_id}",
                        _ms_to_s(session.start_ms),
                        _ms_to_s(session.end_ms),
                    )
                )

    def _build_tools(self) -> None:
        for session_key, tools in self.tools_by_session.items():
            session = self.sessions[session_key]
            session_eid = ids.agent_session_id(session_key)
            for tool in sorted(tools, key=lambda item: item.timestamp_ms):
                retry_index, retry_of = self._retry_context(session_key, tool)
                risk = _tool_risk(tool)
                eid = ids.tool_call_id(tool.id)
                display = f"{tool.tool_name} · {tool.target[:56]}"
                self.entities.append(
                    _entity(
                        "ai.tool_call",
                        eid,
                        display,
                        {
                            "session_id": session_eid,
                            "session_name": _display_session(session),
                            "tool_name": tool.tool_name,
                            "status": tool.status,
                            "timestamp": _ms_to_s(tool.timestamp_ms),
                            "duration_ms": tool.duration_ms or 0,
                            "target": tool.target,
                            "command": _command_of(tool),
                            "retry_index": retry_index,
                            "retry_of": retry_of,
                            "risk": risk,
                            "input_summary": _summarize(tool.input),
                            "output_summary": _summarize(tool.output),
                        },
                        _ms_to_s(tool.timestamp_ms),
                        _ms_to_s(tool.timestamp_ms),
                    )
                )
                self._tool_entities[tool.id] = self.entities[-1]
                self.relations.append(
                    _relation(
                        "ai.agent_session",
                        session_eid,
                        "ai.tool_call",
                        eid,
                        "invokes",
                        f"{_display_session(session)} invokes {tool.tool_name}",
                        _ms_to_s(tool.timestamp_ms),
                        _ms_to_s(tool.timestamp_ms),
                        {"status": tool.status, "risk": risk, "retry_index": retry_index},
                    )
                )

    def _build_effects(self) -> None:
        for index, effect in enumerate(self.agent_snapshot.effects):
            session_key = self._resolve_effect_session(effect)
            risk = _effect_risk(effect)
            raw_id = effect.id or f"{effect.effect_type}-{index}"
            eid = ids.effect_id(raw_id)
            target = effect.target or effect.summary
            self._effect_ids[effect.id] = eid
            self.entities.append(
                _entity(
                    "ai.effect",
                    eid,
                    f"{effect.effect_type}:{effect.action} {target[:48]}",
                    {
                        "effect_type": effect.effect_type,
                        "action": effect.action,
                        "target": target[:300],
                        "status": effect.status,
                        "risk": risk,
                        "timestamp": _ms_to_s(effect.timestamp_ms),
                        "session_name": _display_session(self.sessions[session_key])
                        if session_key
                        else "",
                        "tool_name": "",
                        "details": effect.summary[:300],
                        "confidence": effect.confidence if effect.confidence is not None else 0.9,
                    },
                    _ms_to_s(effect.timestamp_ms),
                    _ms_to_s(effect.timestamp_ms),
                )
            )
            self._effect_entities[effect.id] = self.entities[-1]
            if session_key:
                self._effect_session[effect.id] = session_key
                tool = self._match_tool(session_key, effect)
                if tool:
                    self._effect_tool[effect.id] = tool.id
                    self.entities[-1]["tool_name"] = tool.tool_name
                    self.relations.append(
                        _relation(
                            "ai.tool_call",
                            ids.tool_call_id(tool.id),
                            "ai.effect",
                            eid,
                            "produces",
                            f"{tool.tool_name} produces {effect.effect_type} {effect.action}",
                            _ms_to_s(effect.timestamp_ms),
                            _ms_to_s(effect.timestamp_ms),
                            {"risk": risk, "status": effect.status},
                        )
                    )

    def _link_services(self) -> None:
        service_names = set(self.services)
        for effect in self.agent_snapshot.effects:
            session_key = self._effect_session.get(effect.id)
            if not session_key:
                continue
            target = f"{effect.target} {effect.summary}".lower()
            for name in service_names:
                if name.lower() in target:
                    self.relations.append(
                        _relation(
                            "ai.effect",
                            self._effect_ids.get(effect.id, ids.effect_id(effect.id)),
                            "svc.service",
                            ids.service_id(name),
                            "affects",
                            f"{effect.effect_type} {effect.action} affects {name}",
                            _ms_to_s(effect.timestamp_ms),
                            _ms_to_s(effect.timestamp_ms),
                            {"risk": _effect_risk(effect)},
                        )
                    )

    # ------------------------------------------------------------------ helpers
    def _effects_for_session(self, session_key: str) -> list[Effect]:
        result = []
        for effect in self.agent_snapshot.effects:
            if self._resolve_effect_session(effect) == session_key:
                result.append(effect)
        return result

    def _resolve_effect_session(self, effect: Effect) -> str | None:
        if effect.session_key:
            session = self.aliases.get(effect.session_key)
            if session:
                return session.key
        if effect.pid:
            key = self._pid_to_session.get(effect.pid)
            if key:
                return key
        return None

    def _retry_context(self, session_key: str, tool: ToolCall) -> tuple[int, str]:
        key = f"{session_key}:{tool.tool_name}:{tool.target}"
        previous = self._tool_retry.get(key)
        index = 0
        retry_of = ""
        if previous:
            prev_index, prev_id = previous
            index = prev_index + 1
            retry_of = prev_id
        self._tool_retry[key] = (index, tool.id)
        return index, retry_of

    def _match_tool(self, session_key: str, effect: Effect) -> ToolCall | None:
        candidates = self.tools_by_session.get(session_key, [])
        if not candidates:
            return None
        target = (effect.target or "").lower()
        command = f"{effect.summary} {effect.target}".lower()
        scored = []
        for tool in candidates:
            score = 0
            tool_target = (tool.target or "").lower()
            if tool_target and (tool_target in target or target in tool_target):
                score += 4
            tool_command = _command_of(tool).lower()
            if tool_command and (tool_command in command or command in tool_command):
                score += 3
            if abs(tool.timestamp_ms - effect.timestamp_ms) <= 120_000:
                score += 2
            if effect.effect_type in tool.tool_name.lower():
                score += 1
            score += _tool_intent_bonus(tool.tool_name, effect)
            scored.append((score, tool))
        scored.sort(
            key=lambda item: (item[0], -abs(item[1].timestamp_ms - effect.timestamp_ms)),
            reverse=True,
        )
        return scored[0][1] if scored and scored[0][0] >= 4 else None

    def _loops_for(self, session_key: str, tools: Iterable[ToolCall]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], list[ToolCall]] = defaultdict(list)
        for tool in tools:
            groups[(tool.tool_name, tool.target)].append(tool)
        loops = []
        for (tool_name, target), items in groups.items():
            if len(items) < 2:
                continue
            failures = sum(1 for item in items if item.failed)
            first = min(item.timestamp_ms for item in items)
            last = max(item.timestamp_ms for item in items)
            if failures == 0 and (last - first) > 10 * 60_000:
                continue
            loops.append(
                {
                    "session": session_key,
                    "tool": tool_name,
                    "target": target,
                    "attempts": len(items),
                    "failures": failures,
                    "first_ts": _ms_to_s(first),
                    "last_ts": _ms_to_s(last),
                    "duration_s": round((last - first) / 1000.0, 1),
                }
            )
        return loops

    # ----------------------------------------------------------------- analysis
    def _analyze(self) -> dict[str, Any]:
        sessions = []
        loops = []
        risk_effects = []
        claims = []
        for entity in self.entities:
            if entity.get("__entity_type__") == "ai.agent_session":
                session_key = str(entity.get("session_key"))
                session = self.sessions[session_key]
                tools = self.tools_by_session.get(session_key, [])
                session_loops = self._loops_for(session_key, tools)
                loops.extend(session_loops)
                effects = self._effects_for_session(session_key)
                high_risk = [effect for effect in effects if _effect_risk(effect) == "high"]
                for effect in high_risk:
                    risk_effects.append(
                        {
                            "id": effect.id,
                            "session": _display_session(session),
                            "type": effect.effect_type,
                            "action": effect.action,
                            "target": effect.target,
                            "summary": effect.summary,
                            "ts": _ms_to_s(effect.timestamp_ms),
                            "risk": "high",
                        }
                    )
                reasons = []
                if high_risk:
                    reasons.append(f"{len(high_risk)} high-risk system effect(s)")
                if session_loops:
                    reasons.append(f"{len(session_loops)} retry loop(s)")
                if entity.get("claim_status") == "verified":
                    reasons.append("claimed root cause matches graph evidence")
                if entity.get("claim_status") == "mismatch":
                    reasons.append("claimed root cause conflicts with graph evidence")
                if entity.get("remediation_status") == "pending_validation":
                    reasons.append("remediation not yet validated by eBPF")
                sessions.append(
                    {
                        "id": entity.get("__entity_id__"),
                        "key": session_key,
                        "name": entity.get("display_name"),
                        "agent_type": entity.get("agent_type"),
                        "model": entity.get("model"),
                        "provider": entity.get("provider"),
                        "status": entity.get("status"),
                        "start_ts": entity.get("start_ts"),
                        "end_ts": entity.get("end_ts"),
                        "duration_s": entity.get("duration_s"),
                        "input_tokens": entity.get("input_tokens"),
                        "output_tokens": entity.get("output_tokens"),
                        "total_tokens": entity.get("total_tokens"),
                        "cost_usd": entity.get("cost_usd"),
                        "tool_calls": entity.get("tool_calls"),
                        "failed_tool_calls": entity.get("failed_tool_calls"),
                        "retry_loops": entity.get("retry_loops"),
                        "files_read": entity.get("files_read"),
                        "files_written": entity.get("files_written"),
                        "network_calls": entity.get("network_calls"),
                        "commands": entity.get("commands"),
                        "risk_score": entity.get("risk_score"),
                        "friction_score": entity.get("friction_score"),
                        "risk_level": entity.get("risk_level"),
                        "linked_service": entity.get("linked_service"),
                        "incident_id": entity.get("incident_id"),
                        "claim_root_cause": entity.get("claim_root_cause"),
                        "claim_status": entity.get("claim_status"),
                        "remediation_status": entity.get("remediation_status"),
                        "prompt_preview": entity.get("prompt_preview"),
                        "cwd": entity.get("cwd"),
                        "reasons": reasons,
                        "tool_timeline": [
                            {
                                "id": tool.id,
                                "tool": tool.tool_name,
                                "target": tool.target,
                                "status": tool.status,
                                "ts": _ms_to_s(tool.timestamp_ms),
                                "duration_ms": tool.duration_ms,
                                "risk": _tool_risk(tool),
                            }
                            for tool in sorted(tools, key=lambda item: item.timestamp_ms)
                        ],
                    }
                )
                if entity.get("claim_root_cause"):
                    claims.append(
                        {
                            "session": entity.get("display_name"),
                            "claim": entity.get("claim_root_cause"),
                            "graph_root_cause": self.service_analysis.get("root_cause"),
                            "status": entity.get("claim_status"),
                        }
                    )

        sessions.sort(
            key=lambda item: (float(item.get("risk_score") or 0), float(item.get("cost_usd") or 0)),
            reverse=True,
        )
        loops.sort(key=lambda item: (item["failures"], item["attempts"]), reverse=True)
        risk_effects.sort(key=lambda item: item["ts"])

        model_rows = [
            {
                "id": item.get("__entity_id__"),
                "model": item.get("display_name"),
                "provider": item.get("provider"),
                "calls": item.get("calls"),
                "sessions": item.get("sessions"),
                "tokens": item.get("total_tokens"),
                "cost_usd": item.get("cost_usd"),
            }
            for item in self.entities
            if item.get("__entity_type__") == "ai.model"
        ]
        model_rows.sort(key=lambda item: float(item.get("tokens") or 0), reverse=True)

        total_tokens = sum(int(item.total_tokens) for item in self.agent_snapshot.sessions)
        total_cost = sum(
            model_cost(item.model, item.input_tokens, item.output_tokens)
            for item in self.agent_snapshot.sessions
        )
        findings = self._findings(sessions, loops, risk_effects, claims)
        timeline = self._timeline()
        return {
            "schema_version": 1,
            "source": f"agentsight.{self.agent_snapshot.source_kind}",
            "source_path": Path(self.agent_snapshot.source_path).name,
            "generated_at": self.agent_snapshot.generated_at,
            "summary": {
                "sessions": len(self.agent_snapshot.sessions),
                "models": len(model_rows),
                "tool_calls": sum(len(items) for items in self.tools_by_session.values()),
                "failed_tool_calls": sum(
                    1 for tool in self.agent_snapshot.tool_calls if tool.failed
                ),
                "retry_loops": len(loops),
                "total_tokens": total_tokens,
                "cost_usd": round(total_cost, 4),
                "high_risk_effects": len(risk_effects),
                "claims_verified": sum(1 for item in claims if item["status"] == "verified"),
                "claims_mismatched": sum(1 for item in claims if item["status"] == "mismatch"),
                "agent_entities": len(
                    [item for item in self.entities if item.get("__domain__") == "ai"]
                ),
                "agent_relations": len(
                    [
                        item
                        for item in self.relations
                        if item.get("__src_domain__") == "ai" or item.get("__dest_domain__") == "ai"
                    ]
                ),
            },
            "models": model_rows,
            "sessions": sessions,
            "loops": loops,
            "risk_effects": risk_effects,
            "claims": claims,
            "findings": findings,
            "timeline": timeline,
        }

    def _findings(
        self,
        sessions: list[dict[str, Any]],
        loops: list[dict[str, Any]],
        risk_effects: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        findings = []
        for loop in loops:
            findings.append(
                {
                    "id": "agent-retry-loop",
                    "severity": "warning" if loop["failures"] < 2 else "critical",
                    "title": f"{loop['tool']} retry loop",
                    "detail": f"{loop['session']} attempted {loop['tool']} {loop['attempts']} times against {loop['target'][:100]} ({loop['failures']} failures) over {loop['duration_s']:.0f}s.",
                    "entities": [ids.agent_session_id(loop["session"])],
                    "session": loop["session"],
                }
            )
        if risk_effects:
            findings.append(
                {
                    "id": "agent-risky-effects",
                    "severity": "critical",
                    "title": "Agent touched high-risk system surfaces",
                    "detail": f"{len(risk_effects)} high-risk effects, including {risk_effects[0]['action']} {risk_effects[0]['target'][:120]}.",
                    "entities": [],
                    "session": risk_effects[0]["session"],
                }
            )
        for claim in claims:
            findings.append(
                {
                    "id": "agent-claim-verified"
                    if claim["status"] == "verified"
                    else "agent-claim-mismatch",
                    "severity": "info" if claim["status"] == "verified" else "critical",
                    "title": "Agent root cause verified against graph"
                    if claim["status"] == "verified"
                    else "Agent root cause conflicts with graph",
                    "detail": f"{claim['session']} claimed {claim['claim']}; graph RCA says {claim['graph_root_cause']} ({claim['status']}).",
                    "entities": [],
                    "session": claim["session"],
                }
            )
        pending = [
            item for item in sessions if item.get("remediation_status") == "pending_validation"
        ]
        for session in pending:
            findings.append(
                {
                    "id": "agent-remediation-pending",
                    "severity": "warning",
                    "title": "Remediation is not yet validated by eBPF",
                    "detail": f"{session['name']} changed {session.get('linked_service')}, but the service is still {self.services.get(str(session.get('linked_service')), {}).get('status', 'unknown')} in the same window.",
                    "entities": [session.get("id")],
                    "session": session["name"],
                }
            )
        return findings

    def _timeline(self) -> list[dict[str, Any]]:
        events = []
        for session in self.agent_snapshot.sessions:
            events.append(
                {
                    "ts": _ms_to_s(session.start_ms),
                    "kind": "agent",
                    "title": f"{_display_session(session)} started",
                    "detail": f"{session.agent_type} · {session.model}",
                    "session": _display_session(session),
                }
            )
            if session.end_ms:
                events.append(
                    {
                        "ts": _ms_to_s(session.end_ms),
                        "kind": "agent",
                        "title": f"{_display_session(session)} ended",
                        "detail": f"{session.total_tokens} tokens · {session.duration_s / 60:.1f} min",
                        "session": _display_session(session),
                    }
                )
        for tool in self.agent_snapshot.tool_calls:
            session = self.aliases.get(tool.session_key)
            if not session:
                continue
            events.append(
                {
                    "ts": _ms_to_s(tool.timestamp_ms),
                    "kind": "tool",
                    "title": f"{tool.tool_name} {'failed' if tool.failed else 'succeeded'}",
                    "detail": tool.target[:160],
                    "session": _display_session(session),
                }
            )
        for effect in self.agent_snapshot.effects:
            if _effect_risk(effect) != "high":
                continue
            events.append(
                {
                    "ts": _ms_to_s(effect.timestamp_ms),
                    "kind": "effect",
                    "title": f"high-risk {effect.effect_type} {effect.action}",
                    "detail": effect.target[:160],
                    "session": _display_session(self.aliases[effect.session_key])
                    if effect.session_key in self.aliases
                    else "",
                }
            )
        events.sort(key=lambda item: item["ts"])
        for event in events:
            event["clock"] = clock(event["ts"]) if event["ts"] else "--:--"
        return events


def _summarize(value: Any, limit: int = 160) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{key}={_summarize(val, 40)}" for key, val in list(value.items())[:4])[
            :limit
        ]
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    return str(value)[:limit]


def _entity(
    type_name: str, eid: str, display: str, fields: dict[str, Any], first: int, last: int
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "__domain__": type_name.split(".", 1)[0],
        "__entity_type__": type_name,
        "__entity_id__": eid,
        "__category__": "entity",
        "__method__": "Update",
        "__first_observed_time__": int(first or SCENARIO_START),
        "__last_observed_time__": int(last or first or SCENARIO_START),
        "__keep_alive_seconds__": 3600,
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
        "__first_observed_time__": int(first or SCENARIO_START),
        "__last_observed_time__": int(last or first or SCENARIO_START),
        "__keep_alive_seconds__": 3600,
        "display_name": display,
    }
    if fields:
        payload.update(fields)
    return payload
