"""Materialize code-level RCA/REA results into the UModel code domain.

The service graph answers "which service"; this graph answers "which file,
function, line, patch, and verification". It is deliberately small: only the
code locations touched by the ten financial incidents are represented, so the
graph stays queryable and does not explode into every line of the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ids
from .code_rca import CodeIndex
from .config import SCENARIO_START

SERVICE_MAP = {
    "payment-orchestrator": "checkout-service",
    "fx-pricing": "checkout-service",
    "billing": "checkout-service",
    "settlement": "ledger-service",
    "refund-service": "checkout-service",
    "ledger": "ledger-service",
    "payout-router": "payment-router",
    "risk-gateway": "risk-control",
    "settlement-calendar": "ledger-service",
}


def _entity(
    type_name: str,
    eid: str,
    display: str,
    fields: dict[str, Any],
    first: int = SCENARIO_START,
    last: int = SCENARIO_START,
) -> dict[str, Any]:
    payload = {
        "__domain__": type_name.split(".", 1)[0],
        "__entity_type__": type_name,
        "__entity_id__": eid,
        "__category__": "entity",
        "__method__": "Update",
        "__first_observed_time__": int(first),
        "__last_observed_time__": int(last),
        "__keep_alive_seconds__": 86400,
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
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "__src_domain__": src_type.split(".", 1)[0],
        "__src_entity_type__": src_type,
        "__src_entity_id__": src_id,
        "__dest_domain__": dest_type.split(".", 1)[0],
        "__dest_entity_type__": dest_type,
        "__dest_entity_id__": dest_id,
        "__relation_type__": relation_type,
        "__category__": "entity_link",
        "__method__": "Update",
        "__first_observed_time__": SCENARIO_START,
        "__last_observed_time__": SCENARIO_START,
        "__keep_alive_seconds__": 86400,
        "display_name": display,
    }
    if fields:
        payload.update(fields)
    return payload


def build_code_graph(
    rea_result: dict[str, Any],
    repo_root: str | Path,
    snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = Path(repo_root)
    index = CodeIndex(root)
    repo_name = str(rea_result.get("repo") or "demo/fintech")
    repo_eid = ids.code_repo_id(repo_name)
    entities: list[dict[str, Any]] = [
        _entity(
            "code.repository",
            repo_eid,
            repo_name,
            {
                "repo_path": str(root),
                "branch": "",
                "commit": "",
                "language": "python",
                "status": "active",
            },
        )
    ]
    relations: list[dict[str, Any]] = []
    files: dict[str, str] = {}
    functions: dict[str, str] = {}
    service_names = {
        str(item.get("display_name"))
        for item in (snapshot or {}).get("entities", [])
        if item.get("__entity_type__") == "svc.service"
    }

    for iteration in rea_result.get("iterations", []):
        case_id = str(iteration.get("case_id"))
        case = iteration
        finding = (iteration.get("rca") or {}).get("finding") or {}
        file_path = str(finding.get("file") or "")
        line = int(finding.get("line") or 0)
        function_name = str(finding.get("function") or "")
        if not file_path or not function_name:
            continue
        file_eid = files.get(file_path)
        if not file_eid:
            file_eid = ids.code_file_id(file_path)
            files[file_path] = file_eid
            line_count = (
                len((root / file_path).read_text(encoding="utf-8").splitlines())
                if (root / file_path).exists()
                else 0
            )
            entities.append(
                _entity(
                    "code.file",
                    file_eid,
                    file_path,
                    {
                        "path": file_path,
                        "language": "python",
                        "lines": line_count,
                        "status": "changed",
                    },
                )
            )
            relations.append(
                _relation(
                    "code.repository",
                    repo_eid,
                    "code.file",
                    file_eid,
                    "contains",
                    f"{repo_name} contains {file_path}",
                )
            )
        function_eid = functions.get(f"{file_path}:{function_name}")
        if not function_eid:
            function_eid = ids.code_function_id(file_path, function_name)
            functions[f"{file_path}:{function_name}"] = function_eid
            entities.append(
                _entity(
                    "code.function",
                    function_eid,
                    f"{file_path}:{function_name}",
                    {
                        "qualname": function_name,
                        "file": file_path,
                        "start_line": int(
                            (index.function_at(file_path, line) or {}).get("line") or line
                        ),
                        "end_line": int(
                            (index.function_at(file_path, line) or {}).get("end_line") or line
                        ),
                        "status": "fixed",
                    },
                )
            )
            relations.append(
                _relation(
                    "code.file",
                    file_eid,
                    "code.function",
                    function_eid,
                    "contains",
                    f"{file_path} contains {function_name}",
                )
            )
        line_eid = ids.code_line_id(file_path, line)
        entities.append(
            _entity(
                "code.line",
                line_eid,
                f"{file_path}:{line}",
                {
                    "file": file_path,
                    "line": line,
                    "function": function_name,
                    "source": str(finding.get("source") or ""),
                    "self_ms": float(finding.get("self_ms") or 0),
                    "p95_ms": float(finding.get("p95_ms") or 0),
                    "error_rate": float(finding.get("error_rate") or 0),
                    "changed": bool(finding.get("changed")),
                    "score": float(finding.get("score") or 0),
                    "confidence": float(finding.get("confidence") or 0),
                    "status": "fixed",
                },
            )
        )
        relations.append(
            _relation(
                "code.function",
                function_eid,
                "code.line",
                line_eid,
                "has_line",
                f"{function_name} has {line}",
            )
        )
        issue_eid = ids.code_issue_id(case_id)
        entities.append(
            _entity(
                "code.issue",
                issue_eid,
                f"{case_id} {case.get('title', '')}",
                {
                    "case_id": case_id,
                    "severity": str(case.get("severity") or ""),
                    "symptom": str(case.get("symptom") or ""),
                    "status": str(case.get("status") or ""),
                    "slo_p99_ms": float(
                        (iteration.get("verification") or {}).get("slo_p99_ms") or 0
                    ),
                    "p99_before_ms": float(
                        (iteration.get("verification") or {}).get("p99_before_ms") or 0
                    ),
                    "p99_after_ms": float(
                        (iteration.get("verification") or {}).get("p99_after_ms") or 0
                    ),
                    "error_before": float(
                        (iteration.get("verification") or {}).get("error_before") or 0
                    ),
                    "error_after": float(
                        (iteration.get("verification") or {}).get("error_after") or 0
                    ),
                    "confidence": float(finding.get("confidence") or 0),
                },
            )
        )
        relations += [
            _relation(
                "code.line",
                line_eid,
                "code.issue",
                issue_eid,
                "observed_in",
                f"{file_path}:{line} observed in {case_id}",
            ),
            _relation(
                "code.issue",
                issue_eid,
                "code.function",
                function_eid,
                "affects",
                f"{case_id} affects {function_name}",
            ),
        ]
        patch_eid = ids.code_patch_id(case_id)
        entities.append(
            _entity(
                "code.patch",
                patch_eid,
                f"{case_id} patch",
                {
                    "case_id": case_id,
                    "file": file_path,
                    "function": function_name,
                    "strategy": str((iteration.get("pseudocode") or {}).get("strategy") or ""),
                    "diff": str((iteration.get("patch") or {}).get("diff") or "")[:4000],
                    "test": "PASS"
                    if (iteration.get("tests") or {}).get("case", {}).get("passed")
                    else "FAIL",
                    "verification": str((iteration.get("verification") or {}).get("verdict") or ""),
                    "status": str(case.get("status") or ""),
                },
            )
        )
        relations += [
            _relation(
                "code.patch",
                patch_eid,
                "code.issue",
                issue_eid,
                "fixes",
                f"{case_id} patch fixes issue",
            ),
            _relation(
                "code.patch",
                patch_eid,
                "code.file",
                file_eid,
                "modifies",
                f"{case_id} patch modifies {file_path}",
            ),
        ]
        linked = SERVICE_MAP.get(str(case.get("service") or ""))
        if linked and linked in service_names:
            relations.append(
                _relation(
                    "code.issue",
                    issue_eid,
                    "svc.service",
                    ids.service_id(linked),
                    "affects",
                    f"{case_id} affects {linked}",
                )
            )

    summary = {
        "repositories": 1,
        "files": len(files),
        "functions": len(functions),
        "issues": sum(1 for item in entities if item.get("__entity_type__") == "code.issue"),
        "patches": sum(1 for item in entities if item.get("__entity_type__") == "code.patch"),
        "entities": len(entities),
        "relations": len(relations),
    }
    return entities, relations, summary
