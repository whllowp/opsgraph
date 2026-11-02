"""Candidate patch proposals and safety checks for the REA loop.

The demo uses reviewed patches. A patch is never trusted because it is a
candidate: it must apply to exactly one location, stay inside the target source
file, pass the financial regression, and then pass eBPF verification. The
``case-03`` partial-boundary candidate deliberately fails so the loop exercises
rejection and fallback instead of only the happy path.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PatchCandidate:
    candidate_id: str
    strategy: str
    old: str
    new: str
    rationale: str
    source: str = "reviewed"
    safety: dict[str, Any] = field(default_factory=dict)

    def diff(self, file: str) -> str:
        return "".join(
            difflib.unified_diff(
                self.old.splitlines(keepends=True),
                self.new.splitlines(keepends=True),
                fromfile=f"a/{file}",
                tofile=f"b/{file}",
            )
        )

    def as_dict(self, file: str) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strategy": self.strategy,
            "source": self.source,
            "rationale": self.rationale,
            "safety": self.safety,
            "file": file,
            "diff": self.diff(file),
        }


def _reviewed(case: dict[str, Any]) -> PatchCandidate:
    return PatchCandidate(
        candidate_id=f"{case['id']}-reviewed",
        strategy=case["strategy"],
        old=case["patch"]["old"],
        new=case["patch"]["new"],
        rationale="reviewed REA patch for this failure mode",
        source="reviewed",
    )


def _partial_boundary(case: dict[str, Any]) -> PatchCandidate | None:
    if case["id"] != "case-03":
        return None
    old = (
        "    if amount > 10000:\n"
        "        return round(amount * 0.005, 2)\n"
        "    if amount > 1000:\n"
        "        return round(amount * 0.01, 2)\n"
    )
    new = (
        "    if amount > 10000:\n"
        "        return round(amount * 0.005, 2)\n"
        "    if amount >= 1000:\n"
        "        return round(amount * 0.01, 2)\n"
    )
    return PatchCandidate(
        candidate_id=f"{case['id']}-partial-boundary",
        strategy="fix only the 1000 boundary",
        old=old,
        new=new,
        rationale="partial fix: the 10000 boundary remains exclusive",
        source="template",
    )


def candidates_for(case: dict[str, Any], source_text: str) -> list[PatchCandidate]:
    candidates: list[PatchCandidate] = []
    partial = _partial_boundary(case)
    if partial and partial.old in source_text:
        candidates.append(partial)
    candidates.append(_reviewed(case))
    result = []
    seen = set()
    for candidate in candidates:
        key = (candidate.old, candidate.new)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def safety_check(candidate: PatchCandidate, source_text: str, file: str) -> dict[str, Any]:
    matched = source_text.count(candidate.old)
    changed_lines = len(candidate.new.splitlines()) - len(candidate.old.splitlines())
    allowed = matched == 1 and "test" not in file.lower() and abs(changed_lines) <= 80
    return {
        "allowed": allowed,
        "matched": matched,
        "changed_lines": abs(changed_lines),
        "test_file_touched": "test" in file.lower(),
        "reasons": []
        if allowed
        else [
            "patch context must match exactly once"
            if matched != 1
            else "patch touches a test file"
            if "test" in file.lower()
            else "patch too large",
        ],
    }


def apply_candidate(repo: Path, file: str, candidate: PatchCandidate) -> dict[str, Any]:
    path = repo / file
    text = path.read_text(encoding="utf-8")
    safety = safety_check(candidate, text, file)
    candidate.safety = safety
    if not safety["allowed"]:
        return {"applied": False, "safety": safety}
    path.write_text(text.replace(candidate.old, candidate.new, 1), encoding="utf-8")
    return {"applied": True, "safety": safety}


def restore_candidate(repo: Path, file: str, candidate: PatchCandidate) -> None:
    path = repo / file
    text = path.read_text(encoding="utf-8")
    if candidate.new in text:
        path.write_text(text.replace(candidate.new, candidate.old, 1), encoding="utf-8")
