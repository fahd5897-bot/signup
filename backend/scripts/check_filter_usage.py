#!/usr/bin/env python3
"""Fail the build if a Qdrant filter is built outside the isolation module.

``app/rag/vectorstore/filters.py`` is the single choke point for tenant
scoping in the vector store, and its value comes entirely from being the only
one. A hand-rolled ``models.Filter(...)`` elsewhere is not a style problem: a
missing or misspelled tenant condition does not raise, Qdrant simply matches
differently, and the result is either silent cross-tenant leakage or an empty
result set that reads as "no matching content".

Run from the backend directory:  python scripts/check_filter_usage.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The one module allowed to construct filters, and the tests that assert on
#: what it produces.
ALLOWED = {
    ROOT / "app" / "rag" / "vectorstore" / "filters.py",
    ROOT / "scripts" / "check_filter_usage.py",
}

#: Constructors that produce a Qdrant filter or one of its conditions.
FORBIDDEN_ATTRS = {"Filter", "FieldCondition", "IsNullCondition", "HasIdCondition"}


def offending_lines(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # models.Filter(...) / rest.Filter(...) — an attribute access on any
        # module alias. Matching on the attribute name rather than the alias
        # means `import qdrant_client.models as m` is caught too.
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRS:
            found.append((node.lineno, f"{_dotted(func)}(...)"))
        # from qdrant_client.models import Filter; Filter(...)
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_ATTRS:
            found.append((node.lineno, f"{func.id}(...)"))
    return found


def _dotted(node: ast.Attribute) -> str:
    parts = [node.attr]
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def main() -> int:
    violations: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        if path in ALLOWED:
            continue
        for lineno, call in offending_lines(path):
            violations.append(f"{path.relative_to(ROOT)}:{lineno}: {call}")

    if violations:
        print("Qdrant filters must come from app/rag/vectorstore/filters.py:\n")
        print("\n".join(f"  {v}" for v in violations))
        print(
            "\nAdd a builder to filters.py instead. Tenant isolation is only "
            "reviewable while it lives in one file."
        )
        return 1

    print("filter usage: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
