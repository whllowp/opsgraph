"""Optional LLM brain for alert triage.

The LLM is a *proposer*, not an executor. It receives a compact evidence pack
and must return strict JSON; ``brain.py`` validates it against the graph
candidates and Laya's guardrails. Without credentials it is simply disabled and
the explainable Laya model is used alone.

Environment (OpenAI-compatible chat completions):

* ``OPSGRAPH_LLM_URL``      e.g. ``https://api.openai.com/v1/chat/completions``
* ``OPSGRAPH_LLM_API_KEY``
* ``OPSGRAPH_LLM_MODEL``    default ``gpt-4o-mini``
* ``OPSGRAPH_LLM_TIMEOUT``  seconds, default 8
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

VALID_ACTIONS = {"page", "investigate", "watch", "suppress"}
SYSTEM_PROMPT = """You are an alert triage model inside an operations graph.
Return ONLY a JSON object. Do not call tools, do not execute commands.
The only allowed actions are: page, investigate, watch, suppress.
Use graph evidence, not intuition. If evidence is weak, prefer investigate or watch.
JSON schema:
{"root_cause": string, "confidence": number 0..1,
 "false_alarm_probability": number 0..1, "action": string,
 "reason": string, "citations": [string]}"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM did not return JSON")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM JSON root must be an object")
    return value


class LLMBrain:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        transport: Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.url = url if url is not None else os.environ.get("OPSGRAPH_LLM_URL", "")
        self.api_key = (
            api_key if api_key is not None else os.environ.get("OPSGRAPH_LLM_API_KEY", "")
        )
        self.model = model or os.environ.get("OPSGRAPH_LLM_MODEL", "gpt-4o-mini")
        self.timeout = timeout or int(os.environ.get("OPSGRAPH_LLM_TIMEOUT", "8"))
        self.transport = transport or self._http
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model if self.enabled else "",
            "reason": "" if self.enabled else "OPSGRAPH_LLM_URL/API_KEY not configured",
            "last_error": self.last_error,
        }

    def _http(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

    def propose(
        self,
        group: dict[str, Any],
        candidates: list[str],
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "reason": self.status()["reason"]}
        evidence = {
            "group": {
                "title": group.get("title"),
                "severity": group.get("severity"),
                "member_count": group.get("member_count"),
                "inhibited_count": group.get("inhibited_count"),
                "targets": sorted(
                    {str(item.get("target")) for item in (group.get("members") or [])}
                ),
                "rca": group.get("rca") or {},
            },
            "candidate_services": candidates,
            "instruction": "Pick root_cause only from candidate_services. Return JSON only.",
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            response = self.transport(self.url, headers, payload)
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json(str(content))
            root = str(parsed.get("root_cause") or "")
            if candidates and root not in candidates:
                return {
                    "status": "rejected",
                    "reason": f"root_cause {root!r} not in graph candidates",
                    "raw": parsed,
                }
            action = str(parsed.get("action") or "")
            if action not in VALID_ACTIONS:
                return {
                    "status": "rejected",
                    "reason": f"invalid action {action!r}",
                    "raw": parsed,
                }
            false_alarm = float(parsed.get("false_alarm_probability") or 0)
            confidence = float(parsed.get("confidence") or 0)
            if not (0 <= false_alarm <= 1 and 0 <= confidence <= 1):
                return {
                    "status": "rejected",
                    "reason": "probabilities out of range",
                    "raw": parsed,
                }
            return {
                "status": "ok",
                "model": self.model,
                "root_cause": root,
                "confidence": round(confidence, 4),
                "false_alarm_probability": round(false_alarm, 4),
                "action": action,
                "reason": str(parsed.get("reason") or ""),
                "citations": list(parsed.get("citations") or [])[:5],
            }
        except Exception as exc:
            self.last_error = str(exc)
            return {"status": "error", "reason": str(exc)}
