from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.ingestion.parser import ParsedFile


@dataclass(slots=True)
class RelationshipEdge:
    repo_id: str
    from_file: str
    to_file: str
    type: str
    weight: int


def build_relationship_documents(
    *,
    repo_id: str,
    parsed_files: list[ParsedFile],
    source_by_path: dict[str, str],
) -> list[dict[str, object]]:
    """
    Build weighted relationship edges across files.

    Extracted relationship types:
    - imports: resolved imports between files
    - calls: function calls resolved to uniquely-defined functions in other files
    - extends: class inheritance resolved to uniquely-defined classes in other files
    """
    function_locations = _build_name_index(
        ((function.name, parsed.path) for parsed in parsed_files for function in parsed.functions)
    )
    class_locations = _build_name_index(
        ((klass.name, parsed.path) for parsed in parsed_files for klass in parsed.classes)
    )

    weighted_edges: Counter[tuple[str, str, str]] = Counter()

    # imports
    for parsed in parsed_files:
        for imp in parsed.imports:
            if imp.resolved_path and imp.resolved_path != parsed.path:
                weighted_edges[(parsed.path, imp.resolved_path, "imports")] += 1

    # calls
    known_function_names = set(function_locations.keys())
    for parsed in parsed_files:
        source = source_by_path.get(parsed.path, "")
        call_counts = _count_calls_in_source(source, known_function_names)
        for function_name, count in call_counts.items():
            targets = function_locations.get(function_name, set())
            if len(targets) != 1:
                continue
            target = next(iter(targets))
            if target == parsed.path:
                continue
            weighted_edges[(parsed.path, target, "calls")] += count

    # extends
    for parsed in parsed_files:
        source = source_by_path.get(parsed.path, "")
        bases = _extract_base_classes(source)
        for base_name in bases:
            targets = class_locations.get(base_name, set())
            if len(targets) != 1:
                continue
            target = next(iter(targets))
            if target == parsed.path:
                continue
            weighted_edges[(parsed.path, target, "extends")] += 1

    documents: list[dict[str, object]] = []
    for (from_file, to_file, relationship_type), weight in sorted(weighted_edges.items()):
        documents.append(
            {
                "repo_id": repo_id,
                "from_file": from_file,
                "to_file": to_file,
                "type": relationship_type,
                "weight": int(weight),
            }
        )

    return documents


def _build_name_index(pairs: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for name, path in pairs:
        if not name:
            continue
        index[name].add(path)
    return index


def _count_calls_in_source(source: str, known_names: set[str]) -> dict[str, int]:
    """Count likely function calls in source text for known function names."""
    if not source or not known_names:
        return {}

    counts: Counter[str] = Counter()
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip function declarations to reduce false positives.
        if re.match(r"^(def\s+\w+\s*\(|async\s+def\s+\w+\s*\()", stripped):
            continue
        if re.match(r"^(?:export\s+)?function\s+\w+\s*\(", stripped):
            continue

        for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", line):
            if name in known_names:
                counts[name] += 1

    return dict(counts)


def _extract_base_classes(source: str) -> list[str]:
    base_names: list[str] = []

    # Python: class Child(BaseA, BaseB):
    for bases_raw in re.findall(
        r"^\s*class\s+[A-Za-z_]\w*\s*\(([^)]*)\)\s*:", source, flags=re.MULTILINE
    ):
        for base in bases_raw.split(","):
            name = base.strip().split(".")[-1]
            if re.match(r"^[A-Za-z_]\w*$", name):
                base_names.append(name)

    # JS/TS: class Child extends Parent { ... }
    for base in re.findall(r"\bclass\s+[A-Za-z_]\w*\s+extends\s+([A-Za-z_][\w.]*)", source):
        name = base.strip().split(".")[-1]
        if re.match(r"^[A-Za-z_]\w*$", name):
            base_names.append(name)

    return base_names
