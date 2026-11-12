"""GDScript static scanner for performance anti-patterns in hot loops and dead functions."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.models import AuditIssue, IssueCategory, Severity
from gdaudit.utils.godot_project import is_test_script

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import GodotProject

# Built-in Godot virtual lifecycle & engine callbacks that are called automatically by the engine
GODOT_VIRTUAL_METHODS: set[str] = {
    # Core Node & Lifecycle
    "_init",
    "_static_init",
    "_ready",
    "_enter_tree",
    "_exit_tree",
    "_process",
    "_physics_process",
    "_notification",
    "_to_string",
    "_get_configuration_warnings",
    # Input Handling
    "_input",
    "_unhandled_input",
    "_unhandled_key_input",
    "_shortcut_input",
    "_gui_input",
    "_input_event",
    # 2D/3D Rendering & Physics
    "_draw",
    "_integrate_forces",
    # Control & UI Layout
    "_get_minimum_size",
    "_has_point",
    "_clips_input",
    "_make_custom_tooltip",
    "_structured_text_parser",
    # Drag and Drop
    "_can_drop_data",
    "_drop_data",
    "_get_drag_data",
    # Object Property Inspection & Serialization
    "_get_property_list",
    "_set",
    "_get",
    "_validate_property",
    "_property_can_revert",
    "_property_get_revert",
    # Viewport & Editor forwarding
    "_forward_canvas_gui_input",
    "_forward_canvas_draw_over_viewport",
    "_forward_canvas_force_draw_over_viewport",
    "_forward_3d_gui_input",
    "_forward_3d_draw_over_viewport",
    "_forward_3d_force_draw_over_viewport",
    # Iterator protocol
    "_iter_init",
    "_iter_next",
    "_iter_get",
    # EditorPlugin virtuals
    "_build",
    "_clear",
    "_disable_plugin",
    "_edit",
    "_enable_plugin",
    "_get_plugin_name",
    "_get_plugin_icon",
    "_get_state",
    "_get_unsaved_status",
    "_get_window_layout",
    "_handles",
    "_has_main_screen",
    "_make_visible",
    "_save_external_data",
    "_set_state",
    "_set_window_layout",
    "_apply_changes",
    # EditorInspectorPlugin virtuals
    "_can_handle",
    "_parse_begin",
    "_parse_category",
    "_parse_group",
    "_parse_property",
    "_parse_end",
}

# Entry points that test frameworks (GUT, GdUnit4) call by name from inside a test script
TEST_HOOK_METHODS: set[str] = {
    "before_each",
    "after_each",
    "before_all",
    "after_all",
    "prerun_setup",
    "postrun_teardown",
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
        re.compile(r"\bRegEx\.(?:new|create_from_string)\s*\("),
        "RegEx.new()",
        (
            "Compiling regular expressions in hot loop is CPU intensive; "
            "compile once in _ready() or store in a variable."
        ),
    ),
    (
        re.compile(r"\bFileAccess\.open\s*\("),
        "FileAccess.open()",
        (
            "File I/O inside continuous loop causes frame drops; "
            "read/write files asynchronously or outside hot loops."
        ),
    ),
    (
        re.compile(r"\bDirAccess\.open\s*\("),
        "DirAccess.open()",
        (
            "Directory I/O inside continuous loop causes frame drops; "
            "inspect directories outside hot loops."
        ),
    ),
    (
        re.compile(r"\b(?:get_tree\(\)\s*\.\s*)?get_nodes_in_group\s*\("),
        "get_nodes_in_group()",
        (
            "Searching scene tree nodes by group every frame is expensive; "
            "cache group references or use signals."
        ),
    ),
    (
        re.compile(r"\.(?:instantiate|new)\s*\("),
        ".instantiate() / .new()",
        (
            "Object instantiation inside hot loop causes memory churn and GC pressure; "
            "use object pooling."
        ),
    ),
    (
        re.compile(r"\$(?:\"[^\"]+\"|'[^']+'|%?[a-zA-Z0-9_./]+)"),
        "$Node",
        "Shorthand ($) inside hot loop calls get_node() every frame; cache with @onready.",
    ),
]

# Regex matching comments or string tokens in GDScript
_GDSCRIPT_COMMENT_REGEX = re.compile(
    r'("""[\s\S]*?"""|'  # Triple double-quoted multiline string
    r"'''[\s\S]*?'''|"  # Triple single-quoted multiline string
    r'"(?:\\.|[^"\n\\])*"|'  # Regular double-quoted string
    r"'(?:\\.|[^'\n\\])*'|"  # Regular single-quoted string
    r"#[^\r\n]*)"  # Single-line comment
)


@dataclass
class FunctionDef:
    """Information about a function definition in GDScript."""

    name: str
    line_number: int
    indent_level: int
    is_static: bool = False
    is_rpc: bool = False
    body_lines: list[tuple[int, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.body_lines is None:
            self.body_lines = []


def fast_clean_gdscript_comments(source: str) -> str:
    """Mask comment characters preserving line and column structure using C-level regex."""

    def replacer(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("#"):
            return " " * len(token)
        return token

    return _GDSCRIPT_COMMENT_REGEX.sub(replacer, source)


# Backward compatibility alias
clean_gdscript_comments = fast_clean_gdscript_comments


def mask_gdscript_code(source: str) -> str:
    """Mask comments and strings preserving line and column structure for hot loops."""

    def replacer(match: re.Match[str]) -> str:
        token = match.group(0)
        start = match.start()
        if token.startswith("#"):
            return " " * len(token)

        # Preserve quotes for $Node and $%Node syntax
        is_node_path = False
        if start > 0 and source[start - 1] == "$":
            is_node_path = True
        elif start > 1 and source[start - 1] == "%" and source[start - 2] == "$":
            is_node_path = True

        if is_node_path and len(token) >= 2:
            quote = token[0]
            inner_len = len(token) - 2
            return quote + ("_" * inner_len) + quote

        return "".join(c if c in "\r\n" else " " for c in token)

    return _GDSCRIPT_COMMENT_REGEX.sub(replacer, source)


def parse_functions(clean_content: str) -> list[FunctionDef]:
    """Parse declared functions and extract their body lines with line numbers."""
    lines = clean_content.splitlines()
    func_regex = re.compile(r"^([ \t]*)(?:static\s+)?func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    rpc_regex = re.compile(r"^\s*@rpc\b")

    functions: list[FunctionDef] = []
    current_func: FunctionDef | None = None
    pending_rpc: bool = False

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if rpc_regex.match(stripped):
            pending_rpc = True
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
                is_rpc=pending_rpc,
            )
            functions.append(current_func)
            pending_rpc = False
            continue

        # If a non-decorator statement was encountered, reset pending_rpc
        if not stripped.startswith("@"):
            pending_rpc = False

        if current_func is not None:
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

    @staticmethod
    def _is_local_call(line: str, call_pattern: re.Pattern[str]) -> bool:
        """Check if a function is called as a local or self method in the line."""
        for m in call_pattern.finditer(line):
            prefix = line[: m.start()].rstrip()
            if not prefix or not prefix.endswith("."):
                return True
            if re.search(r"\bself\s*\.$", prefix):
                return True
        return False

    def audit_hot_loops(self, file_path: Path, clean_content: str) -> list[AuditIssue]:
        """Detect expensive queries inside _process or _physics_process functions."""
        issues: list[AuditIssue] = []
        try:
            rel_file = file_path.relative_to(self.project.root).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        funcs = parse_functions(clean_content)
        masked_content = mask_gdscript_code(clean_content)
        masked_lines = masked_content.splitlines()

        for fn in funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            for line_no, line_text in fn.body_lines:
                masked_text = masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""
                for regex, op_name, tip in HOT_LOOP_PATTERNS:
                    match = regex.search(masked_text)
                    if match:
                        match_str = line_text[match.start() : match.end()].strip()
                        issues.append(
                            AuditIssue(
                                category=IssueCategory.PERFORMANCE_ANTIPATTERN,
                                severity=Severity.MEDIUM,
                                title=f"Hot Loop Performance Issue: {op_name} in {fn.name}()",
                                description=(
                                    f"In '{rel_file}' line {line_no}, call "
                                    f"'{match_str}' inside '{fn.name}()'. {tip}"
                                ),
                                file_path=rel_file,
                                line_number=line_no,
                                context=line_text.strip(),
                            )
                        )
                        # Avoid multiple reports for the same line if both $ and get_node match
                        break

        # Check 1-level indirect calls from _process or _physics_process
        other_funcs = [f for f in funcs if f.name not in ("_process", "_physics_process")]
        seen_indirect: set[tuple[int, str, str, str]] = set()

        for fn in funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            for callee in other_funcs:
                call_pattern = re.compile(rf"(?:^|(?<=\W)){re.escape(callee.name)}\s*\(")
                first_call_line: int | None = None

                for caller_line_no, caller_line_text in fn.body_lines:
                    caller_masked = (
                        masked_lines[caller_line_no - 1]
                        if caller_line_no - 1 < len(masked_lines)
                        else ""
                    )
                    if self._is_local_call(caller_masked, call_pattern):
                        first_call_line = caller_line_no
                        break

                if first_call_line is None:
                    continue

                for callee_line_no, callee_line_text in callee.body_lines:
                    callee_masked = (
                        masked_lines[callee_line_no - 1]
                        if callee_line_no - 1 < len(masked_lines)
                        else ""
                    )
                    for regex, op_name, tip in HOT_LOOP_PATTERNS:
                        match = regex.search(callee_masked)
                        if match:
                            match_str = callee_line_text[match.start() : match.end()].strip()
                            indirect_key = (callee_line_no, fn.name, callee.name, op_name)
                            if indirect_key in seen_indirect:
                                break
                            seen_indirect.add(indirect_key)
                            issues.append(
                                AuditIssue(
                                    category=IssueCategory.PERFORMANCE_ANTIPATTERN,
                                    severity=Severity.LOW,
                                    title=(
                                        f"Hot Loop Performance Issue (indirect): "
                                        f"{op_name} in {fn.name} -> {callee.name}"
                                    ),
                                    description=(
                                        f"In '{rel_file}' line {callee_line_no}, call "
                                        f"'{match_str}' inside '{callee.name}()' "
                                        f"(indirect call from '{fn.name}()' at "
                                        f"line {first_call_line}: "
                                        f"{fn.name} -> {callee.name}). {tip}"
                                    ),
                                    file_path=rel_file,
                                    line_number=callee_line_no,
                                    context=callee_line_text.strip(),
                                )
                            )
                            break

        return issues

    def audit_dead_functions(self) -> list[AuditIssue]:
        """Detect declared functions that are never called or referenced in the project."""
        issues: list[AuditIssue] = []

        # 1. Parse all functions across all .gd files using in-memory file_texts
        declared_funcs: list[tuple[Path, FunctionDef]] = []
        test_files: set[Path] = set()
        for gd_file in self.project.gd_files:
            content = self.project.file_texts.get(gd_file)
            if content is None:
                try:
                    content = gd_file.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
            cleaned = fast_clean_gdscript_comments(content)
            if is_test_script(cleaned):
                test_files.add(gd_file)
            for fn in parse_functions(cleaned):
                declared_funcs.append((gd_file, fn))

        # 2. Count declarations by name in O(F)
        decl_counts: Counter[str] = Counter(fn.name for _, fn in declared_funcs)

        # 3. Check each function in O(1) in memory against word_counts
        for gd_file, fn in declared_funcs:
            # Skip built-in Godot virtual lifecycle callbacks
            if fn.name in GODOT_VIRTUAL_METHODS:
                continue

            # Skip test methods and hooks the test runner discovers by name (GUT / GdUnit4)
            if gd_file in test_files and (
                fn.name.startswith("test_") or fn.name in TEST_HOOK_METHODS
            ):
                continue

            # Skip RPC decorated methods (called remotely across network)
            if fn.is_rpc:
                continue

            # Skip methods connected to signals in .tscn scene files
            if fn.name in self.project.scene_connected_methods:
                continue

            # If occurrences in the entire project are <= number of declarations,
            # the function is never referenced anywhere in code or scenes.
            if self.project.word_counts.get(fn.name, 0) <= decl_counts[fn.name]:
                rel_file = gd_file.relative_to(self.project.root).as_posix()
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
            content = self.project.file_texts.get(gd_file)
            if content is None:
                try:
                    content = gd_file.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
            cleaned = fast_clean_gdscript_comments(content)
            issues.extend(self.audit_hot_loops(gd_file, cleaned))

        # Audit dead functions
        issues.extend(self.audit_dead_functions())

        return issues
