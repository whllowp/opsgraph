"""Stable UModel entity identifiers.

UModel requires a 128-bit lowercase hex entity id. OpsGraph derives every id
from a semantic natural key (``domain/type/key``) so re-running the pipeline is
idempotent and ids stay stable across DeepFlow replays.
"""

from __future__ import annotations

import hashlib


def entity_id(domain: str, entity_type: str, natural_key: str) -> str:
    payload = f"{domain}/{entity_type}/{natural_key}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def service_id(name: str) -> str:
    return entity_id("svc", "svc.service", name)


def external_id(name: str) -> str:
    return entity_id("svc", "svc.external_service", name)


def endpoint_id(service: str, method: str, path: str) -> str:
    return entity_id("svc", "svc.endpoint", f"{service}:{method.upper()} {path}")


def node_id(name: str) -> str:
    return entity_id("infra", "infra.node", name)


def pod_id(name: str) -> str:
    return entity_id("infra", "infra.pod", name)


def workload_id(name: str) -> str:
    return entity_id("infra", "infra.workload", name)


def namespace_id(name: str) -> str:
    return entity_id("infra", "infra.namespace", name)


def cluster_id(name: str) -> str:
    return entity_id("infra", "infra.cluster", name)


def journey_id(name: str) -> str:
    return entity_id("biz", "biz.journey", name)


def incident_id(name: str) -> str:
    return entity_id("ops", "ops.incident", name)


def change_id(name: str) -> str:
    return entity_id("ops", "ops.change", name)


def alert_id(name: str) -> str:
    return entity_id("ops", "ops.alert", name)
