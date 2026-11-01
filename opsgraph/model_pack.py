"""Generate the UModel model pack from the ontology."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import MODEL_DIR
from .ontology import ENTITY_SETS, LINKS, SCHEMA_URL, SCHEMA_VERSION, entity_set_spec
from .util import write_json


def _element(
    kind: str,
    domain: str,
    name: str,
    display: dict[str, str],
    description: dict[str, str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "schema": {"url": SCHEMA_URL, "version": SCHEMA_VERSION},
        "metadata": {
            "name": name,
            "domain": domain,
            "display_name": display,
            "description": description,
        },
        "spec": spec,
    }


def build_model_pack(root: str | Path | None = None) -> dict[str, Any]:
    target = Path(root or MODEL_DIR)
    entity_dir = target / "entity_set"
    link_dir = target / "link"
    for directory in (entity_dir, link_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    for name, item in ENTITY_SETS.items():
        element = _element(
            "entity_set",
            item["domain"],
            name,
            item["display"],
            item["description"],
            entity_set_spec(name),
        )
        write_json(entity_dir / f"{name}.json", element)

    for item in LINKS:
        spec = {
            "src": {
                "domain": ENTITY_SETS[item["src"]]["domain"],
                "kind": "entity_set",
                "name": item["src"],
            },
            "dest": {
                "domain": ENTITY_SETS[item["dest"]]["domain"],
                "kind": "entity_set",
                "name": item["dest"],
            },
            "entity_link_type": item["type"],
        }
        element = _element(
            "entity_set_link",
            item["domain"],
            item["name"],
            item["display"],
            item["description"],
            spec,
        )
        write_json(link_dir / f"{item['name']}.json", element)

    write_json(
        target / "manifest.json",
        {
            "name": "opsgraph",
            "display_name": {
                "en_us": "OpsGraph eBPF Operational Graph",
                "zh_cn": "OpsGraph eBPF 运维对象图",
            },
            "description": {
                "en_us": "UModel model pack for eBPF-observed services, runtime objects, business journeys, and incident evidence.",
                "zh_cn": "面向 eBPF 观测服务、运行时对象、业务旅程与故障证据的 UModel 模型包。",
            },
            "entity_sets": len(ENTITY_SETS),
            "entity_set_links": len(LINKS),
            "domains": ["infra", "svc", "biz", "ops"],
            "generated_by": "opsgraph.model_pack",
        },
    )
    return {
        "root": str(target),
        "entity_sets": len(ENTITY_SETS),
        "links": len(LINKS),
        "files": len(ENTITY_SETS) + len(LINKS) + 1,
    }
