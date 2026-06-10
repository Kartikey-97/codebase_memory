from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Parser

try:
    from tree_sitter_language_pack import get_parser as ts_get_parser
except Exception:  # noqa: BLE001
    ts_get_parser = None


SUPPORTED_LANGUAGES = {"python", "javascript", "typescript"}

PYTHON_EXTENSIONS = {".py"}
JAVASCRIPT_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs"}
TYPESCRIPT_EXTENSIONS = {".ts", ".tsx"}

JS_TS_IMPORT_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

PYTHON_FUNCTION_NODES = {"function_definition"}
PYTHON_CLASS_NODES = {"class_definition"}
PYTHON_IMPORT_NODES = {"import_statement", "import_from_statement"}
PYTHON_COMPLEXITY_NODES = {
    "if_statement",
    "elif_clause",
    "for_statement",
    "while_statement",
    "except_clause",
    "boolean_operator",
    "conditional_expression",
    "match_statement",
    "case_clause",
}

JS_TS_FUNCTION_NODES = {
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "function_expression",
    "arrow_function",
}
JS_TS_CLASS_NODES = {"class_declaration"}
JS_TS_IMPORT_NODES = {"import_statement"}
JS_TS_COMPLEXITY_NODES = {
    "if_statement",
    "switch_case",
    "for_statement",
    "for_in_statement",
    "for_of_statement",
    "while_statement",
    "do_statement",
    "catch_clause",
    "ternary_expression",
    "conditional_expression",
    "logical_expression",
}


@dataclass(slots=True)
class ParsedFunction:
    name: str
    start_line: int
    end_line: int
    has_docstring: bool
    cyclomatic_complexity: int


@dataclass(slots=True)
class ParsedClass:
    name: str
    start_line: int
    end_line: int


@dataclass(slots=True)
class ParsedImport:
    raw: str
    resolved_path: str | None = None


@dataclass(slots=True)
class ParsedFile:
    path: str
    language: str
    functions: list[ParsedFunction] = field(default_factory=list)
    classes: list[ParsedClass] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    parse_mode: str = "ast"


def parse_file(file_path: str | Path, repo_root: str | Path | None = None) -> ParsedFile:
    path = Path(file_path).resolve()
    root = Path(repo_root).resolve() if repo_root else path.parent
    relative_path = _as_repo_relative(path, root)
    language = _detect_language(path)

    source_text = path.read_text(encoding="utf-8", errors="ignore")
    source_bytes = source_text.encode("utf-8", errors="ignore")

    parser = _get_parser_for_language(language)
    if parser is None:
        return _fallback_parse(relative_path, language, source_text, path, root)

    try:
        tree = _parse_syntax_tree(parser=parser, source_text=source_text, source_bytes=source_bytes)
        root_node = _get_tree_root_node(tree)
        return _parse_with_tree_sitter(
            relative_path=relative_path,
            language=language,
            source_text=source_text,
            source_bytes=source_bytes,
            root_node=root_node,
            file_path=path,
            repo_root=root,
        )
    except Exception:  # noqa: BLE001
        return _fallback_parse(relative_path, language, source_text, path, root)


def parse_files(
    file_paths: list[str | Path], repo_root: str | Path | None = None
) -> list[ParsedFile]:
    return [parse_file(file_path=file_path, repo_root=repo_root) for file_path in file_paths]


def _parse_with_tree_sitter(
    *,
    relative_path: str,
    language: str,
    source_text: str,
    source_bytes: bytes,
    root_node: Any,
    file_path: Path,
    repo_root: Path,
) -> ParsedFile:
    parsed = ParsedFile(path=relative_path, language=language, parse_mode="ast")

    for node in _walk_tree(root_node):
        node_kind = _node_kind(node)

        if _is_function_node(language, node_kind):
            name = _get_function_name(node=node, source_bytes=source_bytes, language=language)
            if name:
                parsed.functions.append(
                    ParsedFunction(
                        name=name,
                        start_line=_node_start_row(node) + 1,
                        end_line=_node_end_row(node) + 1,
                        has_docstring=_has_doc_for_function(
                            node=node,
                            language=language,
                            source_text=source_text,
                            source_bytes=source_bytes,
                        ),
                        cyclomatic_complexity=_estimate_cyclomatic_complexity(
                            function_node=node, language=language
                        ),
                    )
                )

        if _is_class_node(language, node_kind):
            class_name = _node_text(node.child_by_field_name("name"), source_bytes).strip()
            if class_name:
                parsed.classes.append(
                    ParsedClass(
                        name=class_name,
                        start_line=_node_start_row(node) + 1,
                        end_line=_node_end_row(node) + 1,
                    )
                )

        if _is_import_node(language, node_kind):
            raw_import = _node_text(node, source_bytes).strip()
            if raw_import:
                parsed.imports.append(
                    ParsedImport(
                        raw=raw_import,
                        resolved_path=_resolve_import_path(
                            raw_import=raw_import,
                            language=language,
                            file_path=file_path,
                            repo_root=repo_root,
                        ),
                    )
                )

    return parsed


def _fallback_parse(
    relative_path: str,
    language: str,
    source_text: str,
    file_path: Path,
    repo_root: Path,
) -> ParsedFile:
    parsed = ParsedFile(path=relative_path, language=language, parse_mode="fallback")
    lines = source_text.splitlines()

    function_patterns = [
        re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*function\s+([A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\("),
    ]
    class_patterns = [
        re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    ]
    import_patterns = [
        re.compile(r"^\s*import\s+.+"),
        re.compile(r"^\s*from\s+.+\s+import\s+.+"),
        re.compile(r'^\s*const\s+.+=\s*require\(["\'](.+)["\']\)'),
    ]

    for line_number, line in enumerate(lines, start=1):
        for pattern in function_patterns:
            match = pattern.match(line)
            if match:
                parsed.functions.append(
                    ParsedFunction(
                        name=match.group(1),
                        start_line=line_number,
                        end_line=line_number,
                        has_docstring=False,
                        cyclomatic_complexity=0,
                    )
                )
                break

        for pattern in class_patterns:
            match = pattern.match(line)
            if match:
                parsed.classes.append(
                    ParsedClass(name=match.group(1), start_line=line_number, end_line=line_number)
                )
                break

        for pattern in import_patterns:
            if pattern.match(line):
                raw = line.strip()
                parsed.imports.append(
                    ParsedImport(
                        raw=raw,
                        resolved_path=_resolve_import_path(
                            raw_import=raw,
                            language=language,
                            file_path=file_path,
                            repo_root=repo_root,
                        ),
                    )
                )
                break

    return parsed


def _detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PYTHON_EXTENSIONS:
        return "python"
    if suffix in TYPESCRIPT_EXTENSIONS:
        return "typescript"
    if suffix in JAVASCRIPT_EXTENSIONS:
        return "javascript"
    return "unknown"


@lru_cache(maxsize=8)
def _get_language_for_language(language: str) -> Language | None:
    if language == "python":
        return Language(ts_python.language())
    if language == "javascript":
        return Language(ts_javascript.language())
    if language == "typescript":
        return Language(ts_typescript.language_typescript())
    return None


def _get_parser_for_language(language: str) -> Any | None:
    if language not in SUPPORTED_LANGUAGES:
        return None

    language_obj = _get_language_for_language(language)
    if language_obj is not None:
        return _build_parser(language_obj)

    if ts_get_parser is None:
        return None
    try:
        return ts_get_parser(language)
    except Exception:  # noqa: BLE001
        return None


def _build_parser(language_obj: Language) -> Parser:
    # py-tree-sitter 0.25+ supports Parser(language).
    try:
        return Parser(language_obj)
    except TypeError:
        parser = Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(language_obj)
        else:
            parser.language = language_obj
        return parser


def _walk_tree(root_node: Any) -> list[Any]:
    stack = [root_node]
    nodes: list[Any] = []
    while stack:
        current = stack.pop()
        nodes.append(current)
        children = _node_children(current)
        for child in reversed(children):
            stack.append(child)
    return nodes


def _parse_syntax_tree(*, parser: Any, source_text: str, source_bytes: bytes) -> Any:
    try:
        return parser.parse(source_text)
    except TypeError:
        return parser.parse(source_bytes)


def _get_tree_root_node(tree: Any) -> Any:
    root_attr = getattr(tree, "root_node", None)
    if callable(root_attr):
        return root_attr()
    return root_attr


def _is_function_node(language: str, node_type: str) -> bool:
    if language == "python":
        return node_type in PYTHON_FUNCTION_NODES
    if language in {"javascript", "typescript"}:
        return node_type in JS_TS_FUNCTION_NODES
    return False


def _is_class_node(language: str, node_type: str) -> bool:
    if language == "python":
        return node_type in PYTHON_CLASS_NODES
    if language in {"javascript", "typescript"}:
        return node_type in JS_TS_CLASS_NODES
    return False


def _is_import_node(language: str, node_type: str) -> bool:
    if language == "python":
        return node_type in PYTHON_IMPORT_NODES
    if language in {"javascript", "typescript"}:
        return node_type in JS_TS_IMPORT_NODES
    return False


def _node_text(node: Any | None, source_bytes: bytes) -> str:
    if node is None:
        return ""
    start_byte = _node_start_byte(node)
    end_byte = _node_end_byte(node)
    return source_bytes[start_byte:end_byte].decode("utf-8", errors="ignore")


def _node_kind(node: Any) -> str:
    kind_attr = getattr(node, "type", None)
    if isinstance(kind_attr, str):
        return kind_attr

    kind_attr = getattr(node, "kind", None)
    if isinstance(kind_attr, str):
        return kind_attr
    if callable(kind_attr):
        return kind_attr()
    return ""


def _node_start_byte(node: Any) -> int:
    value = getattr(node, "start_byte", 0)
    return value() if callable(value) else int(value)


def _node_end_byte(node: Any) -> int:
    value = getattr(node, "end_byte", 0)
    return value() if callable(value) else int(value)


def _node_start_row(node: Any) -> int:
    pos_attr = getattr(node, "start_point", None)
    if callable(pos_attr):
        point = pos_attr()
        return point.row if hasattr(point, "row") else point[0]
    if pos_attr is not None:
        return pos_attr.row if hasattr(pos_attr, "row") else pos_attr[0]

    pos_attr = getattr(node, "start_position", None)
    if callable(pos_attr):
        point = pos_attr()
        return point.row if hasattr(point, "row") else point[0]
    if pos_attr is not None:
        return pos_attr.row if hasattr(pos_attr, "row") else pos_attr[0]
    return 0


def _node_end_row(node: Any) -> int:
    pos_attr = getattr(node, "end_point", None)
    if callable(pos_attr):
        point = pos_attr()
        return point.row if hasattr(point, "row") else point[0]
    if pos_attr is not None:
        return pos_attr.row if hasattr(pos_attr, "row") else pos_attr[0]

    pos_attr = getattr(node, "end_position", None)
    if callable(pos_attr):
        point = pos_attr()
        return point.row if hasattr(point, "row") else point[0]
    if pos_attr is not None:
        return pos_attr.row if hasattr(pos_attr, "row") else pos_attr[0]
    return 0


def _node_children(node: Any) -> list[Any]:
    children_attr = getattr(node, "children", None)
    if children_attr is not None:
        if callable(children_attr):
            return list(children_attr())
        return list(children_attr)

    child_count = getattr(node, "child_count", 0)
    if callable(child_count):
        child_count = child_count()
    child_fn = getattr(node, "child", None)
    if child_fn is None:
        return []
    return [child_fn(index) for index in range(int(child_count))]


def _named_children(node: Any) -> list[Any]:
    named_children_attr = getattr(node, "named_children", None)
    if named_children_attr is not None:
        if callable(named_children_attr):
            return list(named_children_attr())
        return list(named_children_attr)

    named_child_count = getattr(node, "named_child_count", 0)
    if callable(named_child_count):
        named_child_count = named_child_count()
    named_child_fn = getattr(node, "named_child", None)
    if named_child_fn is None:
        return []
    return [named_child_fn(index) for index in range(int(named_child_count))]


def _node_parent(node: Any) -> Any | None:
    parent_attr = getattr(node, "parent", None)
    if callable(parent_attr):
        return parent_attr()
    return parent_attr


def _get_function_name(node: Any, source_bytes: bytes, language: str) -> str:
    name_node = node.child_by_field_name("name")
    explicit_name = _node_text(name_node, source_bytes).strip()
    if explicit_name:
        return explicit_name

    if language in {"javascript", "typescript"}:
        parent = _node_parent(node)
        if parent is not None and _node_kind(parent) == "variable_declarator":
            parent_name = _node_text(parent.child_by_field_name("name"), source_bytes).strip()
            if parent_name:
                return parent_name
    return ""


def _has_doc_for_function(node: Any, language: str, source_text: str, source_bytes: bytes) -> bool:
    if language == "python":
        body_node = node.child_by_field_name("body")
        if body_node is None:
            return False
        named_children = _named_children(body_node)
        if not named_children:
            return False

        first_stmt = named_children[0]
        first_kind = _node_kind(first_stmt)
        if first_kind in {"string", "concatenated_string"}:
            return True
        if first_kind != "expression_statement":
            return False

        child_nodes = _named_children(first_stmt)
        if not child_nodes:
            return False
        return _node_kind(child_nodes[0]) in {"string", "concatenated_string"}

    if language in {"javascript", "typescript"}:
        node_start_byte = _node_start_byte(node)
        parent = _node_parent(node)
        if parent is not None and _node_kind(parent) == "export_statement":
            node_start_byte = _node_start_byte(parent)
        lookback_start = max(0, node_start_byte - 500)
        prefix = source_text.encode("utf-8", errors="ignore")[
            lookback_start:node_start_byte
        ].decode("utf-8", errors="ignore")
        return bool(re.search(r"/\*\*[\s\S]*?\*/\s*$", prefix))

    return False


def _estimate_cyclomatic_complexity(function_node: Any, language: str) -> int:
    if language == "python":
        branch_nodes = PYTHON_COMPLEXITY_NODES
    elif language in {"javascript", "typescript"}:
        branch_nodes = JS_TS_COMPLEXITY_NODES
    else:
        return 0

    complexity = 0
    nested_function_nodes = PYTHON_FUNCTION_NODES | JS_TS_FUNCTION_NODES

    stack = [function_node]
    while stack:
        node = stack.pop()
        children = _node_children(node)
        for child in children:
            child_kind = _node_kind(child)
            if child is not function_node and child_kind in nested_function_nodes:
                continue
            if child_kind in branch_nodes:
                complexity += 1
            stack.append(child)
    return complexity


def _resolve_import_path(
    *,
    raw_import: str,
    language: str,
    file_path: Path,
    repo_root: Path,
) -> str | None:
    if language == "python":
        return _resolve_python_import(
            raw_import=raw_import, file_path=file_path, repo_root=repo_root
        )
    if language in {"javascript", "typescript"}:
        return _resolve_js_ts_import(
            raw_import=raw_import, file_path=file_path, repo_root=repo_root
        )
    return None


def _resolve_python_import(raw_import: str, file_path: Path, repo_root: Path) -> str | None:
    import_from_match = re.match(r"^\s*from\s+([.\w]+)\s+import\s+.+$", raw_import)
    direct_import_match = re.match(r"^\s*import\s+([A-Za-z_][\w.]*).*$", raw_import)

    if import_from_match:
        module_ref = import_from_match.group(1)
        dot_prefix_match = re.match(r"^(\.+)(.*)$", module_ref)
        if dot_prefix_match:
            dots = dot_prefix_match.group(1)
            module_path = dot_prefix_match.group(2)
            base_dir = file_path.parent
            for _ in range(max(0, len(dots) - 1)):
                base_dir = base_dir.parent
            if module_path:
                base_dir = base_dir / module_path.replace(".", "/")
            return _resolve_python_module_candidate(base_dir, repo_root)

        absolute_base = repo_root / module_ref.replace(".", "/")
        return _resolve_python_module_candidate(absolute_base, repo_root)

    if direct_import_match:
        module_ref = direct_import_match.group(1)
        absolute_base = repo_root / module_ref.replace(".", "/")
        return _resolve_python_module_candidate(absolute_base, repo_root)

    return None


def _resolve_python_module_candidate(base: Path, repo_root: Path) -> str | None:
    candidates = [base.with_suffix(".py"), base / "__init__.py"]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return _as_repo_relative(candidate, repo_root)
    return None


def _resolve_js_ts_import(raw_import: str, file_path: Path, repo_root: Path) -> str | None:
    import_match = re.search(r'from\s+["\']([^"\']+)["\']', raw_import)
    if not import_match:
        import_match = re.search(r'import\s+["\']([^"\']+)["\']', raw_import)
    if not import_match:
        import_match = re.search(r'require\(["\']([^"\']+)["\']\)', raw_import)

    if not import_match:
        return None
    module_spec = import_match.group(1)

    if module_spec.startswith("."):
        base = (file_path.parent / module_spec).resolve()
        return _resolve_js_ts_candidate(base, repo_root)
    if module_spec.startswith("/"):
        base = (repo_root / module_spec.lstrip("/")).resolve()
        return _resolve_js_ts_candidate(base, repo_root)
    if module_spec.startswith("@/"):
        # Check standard Next.js / Vite alias locations
        base_src = (repo_root / "src" / module_spec[2:]).resolve()
        match = _resolve_js_ts_candidate(base_src, repo_root)
        if match:
            return match
        base_root = (repo_root / module_spec[2:]).resolve()
        return _resolve_js_ts_candidate(base_root, repo_root)
    return None


def _resolve_js_ts_candidate(base: Path, repo_root: Path) -> str | None:
    candidates = [base]
    candidates.extend(base.with_suffix(ext) for ext in JS_TS_IMPORT_EXTENSIONS)
    candidates.extend((base / f"index{ext}") for ext in JS_TS_IMPORT_EXTENSIONS)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return _as_repo_relative(candidate, repo_root)
    return None


def _as_repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path.resolve())
