"""Minimal UModel REST client used by the OpsGraph pipeline.

Only public contracts are used:

* ``POST /api/v1/workspaces``
* ``POST /api/v1/umodel/{workspace}/import``
* ``POST /api/v1/entitystore/{workspace}/entities:write``
* ``POST /api/v1/entitystore/{workspace}/relations:write``
* ``POST /api/v1/query/{workspace}/execute``

No server internals, GraphStore-specific calls, or private SDKs are involved.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

import requests

from .config import UMODEL_ADDR, WORKSPACE


class UModelError(RuntimeError):
    pass


class UModelClient:
    def __init__(
        self, addr: str | None = None, workspace: str | None = None, timeout: int = 60
    ) -> None:
        self.addr = (addr or UMODEL_ADDR).rstrip("/")
        self.workspace = workspace or WORKSPACE
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        response = self.session.get(f"{self.addr}/healthz", timeout=10)
        response.raise_for_status()
        return response.json()

    def wait_ready(self, attempts: int = 40, delay: float = 0.5) -> bool:
        import time

        for _ in range(attempts):
            try:
                if self.health().get("status") == "ok":
                    return True
            except Exception:
                pass
            time.sleep(delay)
        return False

    # --------------------------------------------------------------- workspace
    def workspace_exists(self) -> bool:
        response = self.session.get(
            f"{self.addr}/api/v1/workspaces/{self.workspace}", timeout=self.timeout
        )
        return response.status_code == 200

    def ensure_workspace(
        self,
        name: str = "OpsGraph",
        description: str = "eBPF-observed operational graph",
    ) -> dict[str, Any]:
        if self.workspace_exists():
            return {"id": self.workspace, "status": "exists"}
        response = self.session.post(
            f"{self.addr}/api/v1/workspaces",
            json={"id": self.workspace, "name": name, "description": description},
            timeout=self.timeout,
        )
        if response.status_code not in (200, 201):
            raise UModelError(
                f"create workspace failed: {response.status_code} {response.text[:500]}"
            )
        return response.json()

    # ------------------------------------------------------------------- model
    def import_model(self, path: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.addr}/api/v1/umodel/{self.workspace}/import",
            json={"path": path},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise UModelError(f"import model failed: {response.status_code} {response.text[:800]}")
        payload = response.json()
        if payload.get("errors"):
            raise UModelError(
                f"import model returned errors: {json.dumps(payload['errors'], ensure_ascii=False)[:800]}"
            )
        return payload

    # ------------------------------------------------------------------ writes
    def write_entities(
        self, entities: Iterable[dict[str, Any]], batch_size: int = 400
    ) -> dict[str, int]:
        return self._write("entities", list(entities), batch_size)

    def write_relations(
        self, relations: Iterable[dict[str, Any]], batch_size: int = 400
    ) -> dict[str, int]:
        return self._write("relations", list(relations), batch_size)

    def expire_relations(self, ids: Iterable[str]) -> dict[str, int]:
        ids = list(ids)
        if not ids:
            return {"accepted": 0, "failed": 0}
        response = self.session.post(
            f"{self.addr}/api/v1/entitystore/{self.workspace}/relations:expire",
            json={"ids": ids, "reason": "OpsGraph snapshot reconciliation"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise UModelError(
                f"expire relations failed: {response.status_code} {response.text[:800]}"
            )
        payload = response.json()
        return {
            "accepted": int(payload.get("accepted", 0) or 0),
            "failed": int(payload.get("failed", 0) or 0),
        }

    def _write(self, kind: str, items: list[dict[str, Any]], batch_size: int) -> dict[str, int]:
        accepted = 0
        failed = 0
        for offset in range(0, len(items), batch_size):
            batch = items[offset : offset + batch_size]
            response = self.session.post(
                f"{self.addr}/api/v1/entitystore/{self.workspace}/{kind}:write",
                json={kind: batch, "partial_success": True},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise UModelError(
                    f"write {kind} failed: {response.status_code} {response.text[:800]}"
                )
            payload = response.json()
            accepted += int(payload.get("accepted", payload.get("succeeded", 0)) or 0)
            failed += int(payload.get("failed", 0) or 0)
        return {"accepted": accepted, "failed": failed}

    # ------------------------------------------------------------------ queries
    def query(self, spl: str) -> list[dict[str, Any]]:
        response = self.session.post(
            f"{self.addr}/api/v1/query/{self.workspace}/execute",
            json={"query": spl},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise UModelError(f"query failed: {response.status_code} {response.text[:800]}")
        payload = response.json()
        if not payload.get("success", True):
            raise UModelError(f"query returned failure: {payload.get('message')}")
        data = payload.get("data") or {}
        header = data.get("header") or []
        rows = data.get("data") or []
        if not rows:
            return []
        if isinstance(rows[0], dict):
            return rows
        return [dict(zip(header, row)) for row in rows]

    def entities(self, domain: str, name: str, limit: int = 5000) -> list[dict[str, Any]]:
        spl = f".entity with(domain='{domain}', name='{name}') | limit {limit}"
        return self.query(spl)

    def all_relations(self, page_size: int = 100) -> list[dict[str, Any]]:
        """Read every relation, paginating around the Cypher result cap.

        The graph-call path returns at most 100 rows regardless of a larger
        LIMIT, so use SKIP/LIMIT pages.
        """

        relations: list[dict[str, Any]] = []
        offset = 0
        while True:
            spl = (
                ".topo | graph-call cypher(`MATCH (src)-[r]->(dest) "
                f"RETURN src, r AS relation, dest SKIP {offset} LIMIT {page_size}`)"
            )
            rows = self.query(spl)
            if not rows:
                break
            for row in rows:
                relation = row.get("relation") or row.get("r")
                if isinstance(relation, dict):
                    relations.append(relation)
            if len(rows) < page_size:
                break
            offset += page_size
        return relations

    def topo_neighbors(
        self, domain: str, entity_type: str, entity_id: str, depth: int = 1
    ) -> list[dict[str, Any]]:
        spl = (
            f".topo | graph-call getNeighborNodes('full', {depth}, "
            f"[(:\"{domain}@{entity_type}\" {{__entity_id__: '{entity_id}'}})])"
        )
        return self.query(spl)

    def model_count(self) -> dict[str, int]:
        rows = self.query(".umodel | limit 1000")
        counts: dict[str, int] = {}
        for row in rows:
            kind = str(row.get("kind") or "")
            counts[kind] = counts.get(kind, 0) + 1
        return counts
