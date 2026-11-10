"""GDScript static scanner for performance anti-patterns in hot loops and dead functions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.models import AuditIssue, IssueCategory, Severity

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import GodotProject

# Built-in Godot virtual lifecycle & engine callbacks that are called automatically by the engine
GODOT_VIRTUAL_METHODS: set[str] = {
    "_init",
    "_ready",
    "_enter_tree",
    "_exit_tree",
    "_process",
    "_physics_process",
    "_input",
    "_unhandled_input",
    "_unhandled_key_input",
    "_shortcut_input",
    "_draw",
    "_notification",
    "_to_string",
    "_gui_input",
    "_get_property_list",
    "_set",
    "_get",
    "_validate_property",
    "_property_can_revert",
    "_property_get_revert",
    "_input_event",
    "_can_drop_data",
    "_drop_data",
    "_get_drag_data",
    "_make_custom_tooltip",
    "_forward_canvas_gui_input",
    "_forward_3d_gui_input",
}

# Regex to identify expensive operations inside _process / _physics_process
HOT_LOOP_PATTERNS = [
    (
        re.compile(r"\b(get_node|get_node_or_null)\s*\("),
        "get_node()",
        "Dynamic node retrieval in hot loop; cache with @onready or in _ready().",
    ),
    (
        re.compile(r"\b(find_child|find_parent|find_children)\s*\("),
        "find_child()",
        "Recursive scene tree search in hot loop causes major frame drops; cache node references.",
    ),
    (
        re.compile(r"\bResourceLoader\.load\s*\("),
        "ResourceLoader.load()",
        "Disk I/O loading resources inside continuous loop; preload or load asynchronously.",
    ),
    (
        re.compile(r"(?<!pre)\bload\s*\("),
        "load()",
        "Dynamic resource loading in hot loop; use preload() or cache loaded resource in _ready().",
    ),
    (
        re.compile(r"\$(?:\"[^\"]+\"|'[^']+'|%?[a-zA-Z0-9_./]+)"),
        "$Node",
        "Shorthand ($) inside hot loop calls get_node() every frame; cache with @onready.",
    ),
]


@dataclass
class FunctionDef:
    """Information about a function definition in GDScript."""

    name: str
    line_number: int
    indent_level: int
    is_static: bool = False
    body_lines: list[tuple[int, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.body_lines is None:
            self.body_lines = []


def clean_gdscript_comments(source: str) -> str:
    """Mask comment characters preserving line and column structure and string contents."""
    chars = list(source)
    n = len(chars)
    i = 0

    in_single_line_str = False
    in_double_line_str = False
    in_triple_single = False
    in_triple_double = False
    escaped = False

    while i < n:
        if escaped:
            escaped = False
            i += 1
            continue

        if chars[i] == "\\":
            escaped = True
            i += 1
            continue

        # Check for triple quotes
        if (
            not in_single_line_str
            and not in_double_line_str
            and not in_triple_single
            and not in_triple_double
        ):
            if i + 2 < n and chars[i] == '"' and chars[i + 1] == '"' and chars[i + 2] == '"':
                in_triple_double = True
                i += 3
                continue
            if i + 2 < n and chars[i] == "'" and chars[i + 1] == "'" and chars[i + 2] == "'":
                in_triple_single = True
                i += 3
                continue
            if chars[i] == '"':
                in_double_line_str = True
                i += 1
                continue
            if chars[i] == "'":
                in_single_line_str = True
                i += 1
                continue
            if chars[i] == "#":
                # Single-line comment: replace everything up to newline with space
                while i < n and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue
        elif in_triple_double:
            if i + 2 < n and chars[i] == '"' and chars[i + 1] == '"' and chars[i + 2] == '"':
                in_triple_double = False
                i += 3
                continue
        elif in_triple_single:
            if i + 2 < n and chars[i] == "'" and chars[i + 1] == "'" and chars[i + 2] == "'":
                in_triple_single = False
                i += 3
                continue
        elif in_double_line_str:
            if chars[i] == '"':
                in_double_line_str = False
            elif chars[i] == "\n":
                in_double_line_str = False
        elif in_single_line_str:
            if chars[i] == "'":
                in_single_line_str = False
            elif chars[i] == "\n":
                in_single_line_str = False

        i += 1

    return "".join(chars)


def parse_functions(clean_content: str) -> list[FunctionDef]:
    """Parse declared functions and extract their body lines with line numbers."""
    lines = clean_content.splitlines()
    func_regex = re.compile(r"^([ \t]*)(?:static\s+)?func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

    functions: list[FunctionDef] = []
    current_func: FunctionDef | None = None

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        match = func_regex.match(line)
        if match:
            indent = len(match.group(1).replace("\t", "    "))
            name = match.group(2)
            is_static = "static func" in line

            current_func = FunctionDef(
                name=name,
                line_number=idx,
                indent_level=indent,
                is_static=is_static,
            )
            functions.append(current_func)
            continue

        if current_func is not None:
            # Calculate line indentation
            leading = len(line) - len(line.lstrip())
            line_indent = len(line[:leading].replace("\t", "    "))

            if line_indent > current_func.indent_level:
                current_func.body_lines.append((idx, line))
            else:
                # Exited function body
                current_func = None

    return functions


class GDScriptAnalyzer:
    """Audits GDScript files for hot loop performance anti-patterns and candidate dead functions."""

    def __init__(self, project: GodotProject) -> None:
        self.project: GodotProject = project

    def audit_hot_loops(self, file_path: Path, clean_content: str) -> list[AuditIssue]:
        """Detect expensive queries inside _process or _physics_process functions."""
        issues: list[AuditIssue] = []
        rel_file = file_path.relative_to(self.project.root).as_posix()
        funcs = parse_functions(clean_content)

        for fn in funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            for line_no, line_text in fn.body_lines:
                for regex, op_name, tip in HOT_LOOP_PATTERNS:
                    match = regex.search(line_text)
                    if match:
                        issues.append(
                            AuditIssue(
                                category=IssueCategory.PERFORMANCE_ANTIPATTERN,
                                severity=Severity.MEDIUM,
                                title=f"Hot Loop Performance Issue: {op_name} in {fn.name}()",
                                description=(
                                    f"In '{rel_file}' line {line_no}, call "
                                    f"'{match.group(0).strip()}' inside '{fn.name}()'. {tip}"
                                ),
                                file_path=rel_file,
                                line_number=line_no,
                                context=line_text.strip(),
                            )
                        )
                        # Avoid multiple reports for the same line if both $ and get_node match
                        break

        return issues

    def audit_dead_functions(self) -> list[AuditIssue]:
        """Detect declared functions that are never called or referenced in the project."""
        issues: list[AuditIssue] = []

        # 1. Parse all functions across all .gd files
        script_data: list[tuple[Path, str, list[FunctionDef]]] = []
        for gd_file in self.project.gd_files:
            try:
                content = gd_file.read_text(encoding="utf-8", errors="replace")
                cleaned = clean_gdscript_comments(content)
                funcs = parse_functions(cleaned)
                script_data.append((gd_file, cleaned, funcs))
            except OSError:
                continue

        # 2. Build full project text cache for reference lookup
        full_project_texts: list[str] = []
        for _, cleaned, _ in script_data:
            full_project_texts.append(cleaned)
        for tscn in self.project.tscn_files + self.project.tres_files:
            try:
                full_project_texts.append(tscn.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue

        # Join full project texts into single searchable string or list of words
        combined_text = "\n".join(full_project_texts)

        # 3. For each non-virtual function, test if it is referenced more than once
        for gd_file, _, funcs in script_data:
            rel_file = gd_file.relative_to(self.project.root).as_posix()

            for fn in funcs:
                if fn.name in GODOT_VIRTUAL_METHODS:
                    continue

                # Search word occurrences of function name
                pattern = re.compile(rf"\b{re.escape(fn.name)}\b")
                matches = pattern.findall(combined_text)

                # If the function name appears only once (its own declaration line),
                # it is never called, connected to a signal, or used as a Callable anywhere.
                if len(matches) <= 1:
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.DEAD_FUNCTION,
                            severity=Severity.LOW,
                            title=f"Dead Function Candidate: '{fn.name}'",
                            description=(
                                f"Function '{fn.name}()' in '{rel_file}' (line {fn.line_number}) "
                                "is never referenced or called anywhere in the project."
                            ),
                            file_path=rel_file,
                            line_number=fn.line_number,
                            context=f"func {fn.name}(...)",
                        )
                    )

        return issues

    def analyze(self) -> list[AuditIssue]:
        """Run all GDScript static audits and return aggregated issues."""
        issues: list[AuditIssue] = []

        # Audit hot loops
        for gd_file in self.project.gd_files:
            try:
                content = gd_file.read_text(encoding="utf-8", errors="replace")
                cleaned = clean_gdscript_comments(content)
                issues.extend(self.audit_hot_loops(gd_file, cleaned))
            except OSError:
                continue

        # Audit dead functions
        issues.extend(self.audit_dead_functions())

        return issues
