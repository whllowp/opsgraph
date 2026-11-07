"""CODEOWNERS parsing for code-domain ownership.

A brief action that touches code must be owned by the code owner, not by the
service owner. The parser supports the subset used in this repo:

* comments and blank lines
* ``*`` fallback
* path prefixes such as ``/demo/fintech/``
* glob patterns such as ``*.py``

The last matching rule wins, matching GitHub CODEOWNERS semantics.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


class OwnersIndex:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.rules: list[tuple[str, str]] = []
        path = self.root / "CODEOWNERS"
        if path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            self.rules.append((parts[0], parts[1]))

    def owner_for(self, file: str) -> str:
        relative = file.replace("\\", "/").lstrip("/")
        owner = ""
        for pattern, candidate in self.rules:
            if self._match(pattern, relative):
                owner = candidate
        return owner

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        if pattern == "*":
            return True
        cleaned = pattern.lstrip("/")
        if cleaned.endswith("/"):
            return path.startswith(cleaned) or path.startswith(cleaned.rstrip("/") + "/")
        if fnmatch(path, cleaned):
            return True
        return fnmatch(path, f"*/{cleaned}")
