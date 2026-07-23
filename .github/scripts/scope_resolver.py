import os
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# ponytail: lazy-loaded tree-sitter parsers per extension with safe fallback if dependencies missing
_PARSERS = {}


def _get_parser_for_file(file_path: str):
    ext = Path(file_path).suffix.lower()
    if ext in _PARSERS:
        return _PARSERS[ext]

    parser = None
    lang = None

    try:
        from tree_sitter import Language, Parser
        if ext == ".py":
            import tree_sitter_python as tspy
            lang = Language(tspy.language())
        elif ext in (".js", ".jsx", ".mjs"):
            import tree_sitter_javascript as tsjs
            lang = Language(tsjs.language())
        elif ext in (".ts", ".tsx"):
            import tree_sitter_typescript as tsts
            lang_fn = getattr(tsts, "language_tsx", None) if ext == ".tsx" else getattr(tsts, "language_typescript", None)
            if not lang_fn:
                lang_fn = getattr(tsts, "language", None)
            if lang_fn:
                lang = Language(lang_fn())
        elif ext == ".go":
            import tree_sitter_go as tsgo
            lang = Language(tsgo.language())
    except Exception as exc:
        _log.debug(f"Tree-sitter parser not available for {ext}: {exc}")

    if lang:
        try:
            parser = Parser(lang)
        except Exception as exc:
            _log.debug(f"Failed to create parser for {ext}: {exc}")

    _PARSERS[ext] = parser
    return parser


def get_enclosing_scopes(file_path: str, target_lines: list[int]) -> list[str]:
    """
    Given a file path and a list of target line numbers (1-indexed),
    returns a list of enclosing scope strings (e.g. ['class UserService -> def login']).
    """
    if not target_lines or not os.path.exists(file_path):
        return []

    parser = _get_parser_for_file(file_path)
    if not parser:
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            code = f.read()
    except Exception as exc:
        _log.debug(f"Could not read file {file_path}: {exc}")
        return []

    try:
        tree = parser.parse(bytes(code, "utf8"))
    except Exception as exc:
        _log.debug(f"Tree-sitter parse error for {file_path}: {exc}")
        return []

    found_scopes = set()

    for line_num in target_lines:
        scopes = []

        def visit(node):
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            if start_line <= line_num <= end_line:
                if node.type in ("function_definition", "class_definition", "function_declaration", "method_definition", "class_declaration"):
                    name_node = node.child_by_field_name("name")
                    if name_node and name_node.text:
                        name_str = name_node.text.decode("utf-8", errors="ignore")
                        kind = "class" if "class" in node.type else "def"
                        scopes.append(f"{kind} {name_str}")

                for child in node.children:
                    visit(child)

        visit(tree.root_node)
        if scopes:
            found_scopes.add(" -> ".join(scopes))

    return sorted(found_scopes)
