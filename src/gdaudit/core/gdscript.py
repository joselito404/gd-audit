"""GDScript static scanner for hot loops, await, debug prints, unsafe paths, and dead functions."""

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
    "_physics_interpolate",
    "_get_aabb",
    "_get_item_rect",
    # RichTextEffect & VisualShaderNodeCustom
    "_process_custom_fx",
    "_get_code",
    "_get_name",
    # Logger callbacks (Godot 4.5+)
    "_log_error",
    "_log_message",
    # EditorImportPlugin virtuals
    "_import",
    "_get_importer_name",
    "_get_recognized_extensions",
    "_get_save_extension",
    "_get_resource_type",
    "_get_preset_count",
    "_get_preset_name",
    "_get_import_options",
    "_get_option_visibility",
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
        re.compile(r"(?<!pre)(?<!\.)\bload\s*\("),
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
        re.compile(r"\$%?(?:\"[^\"]+\"|'[^']+'|[a-zA-Z0-9_./]+)"),
        "$Node",
        "Shorthand ($) inside hot loop calls get_node() every frame; cache with @onready.",
    ),
    (
        re.compile(r"(?<![a-zA-Z0-9_)\]}\"'])%(?:\"[^\"]+\"|'[^']+'|[a-zA-Z_][a-zA-Z0-9_]*)"),
        "%UniqueNode",
        (
            "Unique node shorthand (%) inside hot loop calls get_node() every frame; "
            "cache with @onready."
        ),
    ),
]

_FUNC_DEF_REGEX = re.compile(r"^([ \t]*)(static\s+)?func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_RPC_REGEX = re.compile(r"^\s*@rpc\b")
_LAMBDA_REGEX = re.compile(r"\bfunc(?:\s+[a-zA-Z_][a-zA-Z0-9_]*)?\s*\(")
_AWAIT_REGEX = re.compile(r"(?<!\.)\bawait\b")
_DEBUG_PRINT_REGEX = re.compile(
    r"(?<!\.)\b(print|print_debug|print_rich|prints|printt|printraw)\s*\("
)
_TOOL_ANNOTATION_REGEX = re.compile(r"^[ \t]*@tool\b", re.MULTILINE)
_GET_NODE_CALL_REGEX = re.compile(r"\b(?:get_node|get_node_or_null)\s*\(")
_QUOTED_ARG_REGEX = re.compile(r"""^\s*r?(?:"([^"\r\n]+)"|'([^'\r\n]+)')\s*\)""")
_DOLLAR_PATH_REGEX = re.compile(r"""^\$(?:"([^"\r\n]+)"|'([^'\r\n]+)'|([a-zA-Z0-9_./\-]+))""")
_DEEP_RELATIVE_PATH_REGEX = re.compile(r"(?:^|/)\.\.(?:/\.\.){2,}(?:/|$)")

# GDScript keywords that can directly precede a %UniqueNode expression
_EXPR_PREFIX_KEYWORDS: frozenset[str] = frozenset(
    {
        "return",
        "if",
        "elif",
        "while",
        "match",
        "when",
        "await",
        "and",
        "or",
        "not",
        "in",
        "is",
        "assert",
        "yield",
    }
)

# Regex matching comments or string tokens in GDScript (supports raw r"..."/r'...' strings
# and matches unclosed triple-quoted strings to EOF to prevent quadratic backtracking)
_GDSCRIPT_COMMENT_REGEX = re.compile(
    r'(?:(?<!\w)r)?"""[\s\S]*?(?:"""|\Z)|'  # Triple double-quoted multiline string (raw/normal)
    r"(?:(?<!\w)r)?'''[\s\S]*?(?:'''|\Z)|"  # Triple single-quoted multiline string (raw/normal)
    r'(?<!\w)r"[^"\n]*"|'  # Raw double-quoted string
    r"(?<!\w)r'[^'\n]*'|"  # Raw single-quoted string
    r'"(?:\\.|[^"\n\\])*"|'  # Regular double-quoted string
    r"'(?:\\.|[^'\n\\])*'|"  # Regular single-quoted string
    r"#[^\r\n]*"  # Single-line comment
)


def _is_unique_node_prefix(text: str, percent_pos: int) -> bool:
    """Return True if '%' at `percent_pos` starts a %UniqueNode expression rather than modulo."""
    i = percent_pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0 or text[i] in "\r\n":
        return True
    ch = text[i]
    if ch in ")]}\"'":
        return False
    if ch.isalnum() or ch == "_":
        j = i
        while j >= 0 and (text[j].isalnum() or text[j] == "_"):
            j -= 1
        token = text[j + 1 : i + 1]
        return token in _EXPR_PREFIX_KEYWORDS
    return True


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

        # Preserve quotes for $Node, $%Node, and %Node syntax (non-raw strings only)
        is_node_path = False
        if token[0] in ('"', "'"):
            if start > 0 and source[start - 1] == "$":
                is_node_path = True
            elif start > 1 and source[start - 1] == "%" and source[start - 2] == "$":
                is_node_path = True
            elif (
                start > 0 and source[start - 1] == "%" and _is_unique_node_prefix(source, start - 1)
            ):
                is_node_path = True

        if is_node_path and len(token) >= 2:
            quote = token[0]
            inner_len = len(token) - 2
            return quote + ("_" * inner_len) + quote

        return "".join(c if c in "\r\n" else " " for c in token)

    return _GDSCRIPT_COMMENT_REGEX.sub(replacer, source)


def _find_signature_colon(masked_segment: str, depth: int) -> tuple[int, int]:
    """Update bracket depth across a signature fragment and return top-level ':' offset or -1."""
    for idx, ch in enumerate(masked_segment):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return depth, idx
    return depth, -1


def _scan_signature_end(masked_segment: str, depth: int) -> tuple[int, bool]:
    """Update bracket depth across a signature fragment and detect top-level ':'."""
    new_depth, colon_offset = _find_signature_colon(masked_segment, depth)
    return new_depth, colon_offset != -1


def parse_functions(clean_content: str, masked_content: str | None = None) -> list[FunctionDef]:
    """Parse declared functions and extract their body lines with line numbers.

    Supports multiline function signatures, multiline @rpc(...) annotations,
    ``static func`` with arbitrary whitespace, and multiline string literals
    containing unindented text at column 0.
    """
    lines = clean_content.splitlines()
    masked = masked_content if masked_content is not None else mask_gdscript_code(clean_content)
    masked_lines = masked.splitlines()

    functions: list[FunctionDef] = []
    current_func: FunctionDef | None = None
    pending_rpc: bool = False
    rpc_paren_depth: int = 0
    in_signature: bool = False
    sig_paren_depth: int = 0

    for idx, line in enumerate(lines, start=1):
        masked_line = masked_lines[idx - 1] if idx - 1 < len(masked_lines) else ""
        masked_stripped = masked_line.strip()
        if not masked_stripped:
            continue

        # Continue consuming a multiline @rpc(...) annotation
        if rpc_paren_depth > 0:
            rpc_paren_depth = max(
                0,
                rpc_paren_depth + masked_stripped.count("(") - masked_stripped.count(")"),
            )
            continue

        # Continue consuming a multiline function signature until top-level ':'
        if in_signature and current_func is not None:
            leading = len(masked_line) - len(masked_line.lstrip())
            line_indent = len(masked_line[:leading].replace("\t", "    "))
            # Safety guard: if malformed signature had no ':' and a new top-level decl starts
            if (
                sig_paren_depth == 0
                and line_indent <= current_func.indent_level
                and not masked_stripped.startswith((")", "]", "}", "->", ":"))
            ):
                in_signature = False
            else:
                sig_paren_depth, ended = _scan_signature_end(masked_line, sig_paren_depth)
                if ended:
                    in_signature = False
                continue

        if _RPC_REGEX.match(masked_stripped):
            pending_rpc = True
            rpc_paren_depth = max(0, masked_stripped.count("(") - masked_stripped.count(")"))
            continue

        match = _FUNC_DEF_REGEX.match(masked_line)
        if match:
            indent = len(match.group(1).replace("\t", "    "))
            is_static = match.group(2) is not None
            name = match.group(3)

            current_func = FunctionDef(
                name=name,
                line_number=idx,
                indent_level=indent,
                is_static=is_static,
                is_rpc=pending_rpc,
            )
            functions.append(current_func)
            pending_rpc = False

            # Check whether the function signature finishes on this line
            sig_paren_depth, ended = _scan_signature_end(masked_line[match.end() - 1 :], 0)
            in_signature = not ended
            continue

        # If a non-decorator statement was encountered, reset pending_rpc
        if not masked_stripped.startswith("@"):
            pending_rpc = False

        if current_func is not None:
            leading = len(masked_line) - len(masked_line.lstrip())
            line_indent = len(masked_line[:leading].replace("\t", "    "))

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
        self._parsed_cache: dict[Path, tuple[str, str, list[FunctionDef]]] = {}

    def _get_parsed_file(self, gd_file: Path) -> tuple[str, str, list[FunctionDef]] | None:
        """Read, clean, mask, and parse a .gd file once and cache the result."""
        cached = self._parsed_cache.get(gd_file)
        if cached is not None:
            return cached

        content = self.project.file_texts.get(gd_file)
        if content is None:
            try:
                content = gd_file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return None

        cleaned = fast_clean_gdscript_comments(content)
        masked = mask_gdscript_code(cleaned)
        funcs = parse_functions(cleaned, masked_content=masked)
        entry = (cleaned, masked, funcs)
        self._parsed_cache[gd_file] = entry
        return entry

    @staticmethod
    def _local_call_starts(line: str, call_pattern: re.Pattern[str]) -> list[int]:
        """Return the columns where a function is called as a local or self method."""
        starts: list[int] = []
        for m in call_pattern.finditer(line):
            prefix = line[: m.start()].rstrip()
            if not prefix or not prefix.endswith(".") or re.search(r"\bself\s*\.$", prefix):
                starts.append(m.start())
        return starts

    @staticmethod
    def _indent_of(line: str) -> int:
        """Indentation width of a line, with tabs counted as 4 spaces (as parse_functions)."""
        leading = len(line) - len(line.lstrip())
        return len(line[:leading].replace("\t", "    "))

    @staticmethod
    def _top_level_colon(line: str) -> int:
        """Column of the first ':' outside brackets, or -1 if there is none."""
        depth = 0
        for col, ch in enumerate(line):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ":" and depth <= 0:
                return col
        return -1

    @classmethod
    def _is_unconditional_call(
        cls,
        body: list[tuple[int, str]],
        position: int,
        call_starts: list[int],
        masked_lines: list[str],
    ) -> bool:
        """Check that a call at `body[position]` runs on every frame of its function.

        The call is conditional when an `if`/`elif`/`else`/`match` header encloses it (by
        indentation) or when it sits after the colon of a one-line guard. Loops do not make
        a call conditional. A call inside the condition of an `if`/`match` line itself is
        evaluated unconditionally (subject to its enclosing headers).
        """

        def masked_at(pos: int) -> str:
            line_no = body[pos][0]
            return masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""

        own = masked_at(position)
        keyword = re.match(r"\s*(if|elif|else|match)\b", own)
        if keyword is not None:
            if keyword.group(1) in ("elif", "else"):
                return False
            colon = cls._top_level_colon(own)
            if colon != -1 and not any(start < colon for start in call_starts):
                return False

        current_indent = cls._indent_of(own)
        for pos in range(position - 1, -1, -1):
            text = masked_at(pos)
            if not text.strip():
                continue
            indent = cls._indent_of(text)
            if indent >= current_indent:
                continue
            current_indent = indent
            if re.match(r"\s*(?:if|elif|else|match)\b", text):
                return False
        return True

    @classmethod
    def _compute_lambda_exclusions(
        cls,
        body: list[tuple[int, str]],
        masked_lines: list[str],
    ) -> dict[int, int]:
        """Map body positions inside inline lambdas to the column from which code is excluded.

        Value ``0`` excludes the entire line (multiline lambda body); a positive column excludes
        matches at or after that column (single-line ``func(...): ...`` body).
        """
        exclusions: dict[int, int] = {}
        lambda_indent: int | None = None
        in_lambda_sig: bool = False
        lambda_sig_depth: int = 0
        lambda_sig_indent: int = 0

        for pos, (line_no, _) in enumerate(body):
            masked = masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""
            if not masked.strip():
                if lambda_indent is not None or in_lambda_sig:
                    exclusions[pos] = 0
                continue

            indent = cls._indent_of(masked)

            if lambda_indent is not None:
                if indent > lambda_indent:
                    exclusions[pos] = 0
                    continue
                lambda_indent = None

            if in_lambda_sig:
                lambda_sig_depth, colon_offset = _find_signature_colon(masked, lambda_sig_depth)
                if colon_offset != -1:
                    in_lambda_sig = False
                    if masked[colon_offset + 1 :].strip():
                        exclusions[pos] = colon_offset + 1
                    else:
                        lambda_indent = lambda_sig_indent
                continue

            m = _LAMBDA_REGEX.search(masked)
            if m is None:
                continue

            open_paren_col = m.end() - 1
            depth, rel_colon = _find_signature_colon(masked[open_paren_col:], 0)
            if rel_colon != -1:
                colon_col = open_paren_col + rel_colon
                if masked[colon_col + 1 :].strip():
                    exclusions[pos] = colon_col + 1
                else:
                    lambda_indent = indent
            else:
                in_lambda_sig = True
                lambda_sig_depth = depth
                lambda_sig_indent = indent

        return exclusions

    def audit_hot_loops(
        self,
        file_path: Path,
        clean_content: str,
        *,
        masked_content: str | None = None,
        funcs: list[FunctionDef] | None = None,
    ) -> list[AuditIssue]:
        """Detect expensive queries inside _process or _physics_process functions."""
        issues: list[AuditIssue] = []
        try:
            rel_file = file_path.relative_to(self.project.root).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        masked = masked_content if masked_content is not None else mask_gdscript_code(clean_content)
        parsed_funcs = (
            funcs if funcs is not None else parse_functions(clean_content, masked_content=masked)
        )
        masked_lines = masked.splitlines()

        hot_lambda_cache: dict[int, dict[int, int]] = {}

        for fn in parsed_funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            lambda_exclusions = self._compute_lambda_exclusions(fn.body_lines, masked_lines)
            hot_lambda_cache[id(fn)] = lambda_exclusions

            for position, (line_no, line_text) in enumerate(fn.body_lines):
                excl_col = lambda_exclusions.get(position)
                if excl_col == 0:
                    continue
                masked_text = masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""
                for regex, op_name, tip in HOT_LOOP_PATTERNS:
                    matches = list(regex.finditer(masked_text))
                    if excl_col is not None:
                        matches = [m for m in matches if m.start() < excl_col]
                    if op_name == "%UniqueNode":
                        matches = [
                            m for m in matches if _is_unique_node_prefix(line_text, m.start())
                        ]
                    if matches:
                        match_starts = [m.start() for m in matches]
                        if not self._is_unconditional_call(
                            fn.body_lines, position, match_starts, masked_lines
                        ):
                            continue
                        match = matches[0]
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
        other_funcs = [f for f in parsed_funcs if f.name not in ("_process", "_physics_process")]
        callee_lambda_cache: dict[int, dict[int, int]] = {}
        seen_indirect: set[tuple[int, str, str, str]] = set()

        for fn in parsed_funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            caller_lambda_exclusions = hot_lambda_cache.get(id(fn), {})

            for callee in other_funcs:
                call_pattern = re.compile(rf"(?:^|(?<=\W)){re.escape(callee.name)}\s*\(")
                first_call_line: int | None = None

                # Only calls that run on every frame (no if/elif/else/match or lambda guarding them)
                for position, (caller_line_no, _caller_text) in enumerate(fn.body_lines):
                    excl_col = caller_lambda_exclusions.get(position)
                    if excl_col == 0:
                        continue
                    caller_masked = (
                        masked_lines[caller_line_no - 1]
                        if caller_line_no - 1 < len(masked_lines)
                        else ""
                    )
                    call_starts = self._local_call_starts(caller_masked, call_pattern)
                    if excl_col is not None:
                        call_starts = [s for s in call_starts if s < excl_col]
                    if call_starts and self._is_unconditional_call(
                        fn.body_lines, position, call_starts, masked_lines
                    ):
                        first_call_line = caller_line_no
                        break

                if first_call_line is None:
                    continue

                callee_exclusions = callee_lambda_cache.get(id(callee))
                if callee_exclusions is None:
                    callee_exclusions = self._compute_lambda_exclusions(
                        callee.body_lines, masked_lines
                    )
                    callee_lambda_cache[id(callee)] = callee_exclusions

                for callee_pos, (callee_line_no, callee_line_text) in enumerate(callee.body_lines):
                    callee_excl_col = callee_exclusions.get(callee_pos)
                    if callee_excl_col == 0:
                        continue
                    callee_masked = (
                        masked_lines[callee_line_no - 1]
                        if callee_line_no - 1 < len(masked_lines)
                        else ""
                    )
                    for regex, op_name, tip in HOT_LOOP_PATTERNS:
                        matches = list(regex.finditer(callee_masked))
                        if callee_excl_col is not None:
                            matches = [m for m in matches if m.start() < callee_excl_col]
                        if op_name == "%UniqueNode":
                            matches = [
                                m
                                for m in matches
                                if _is_unique_node_prefix(callee_line_text, m.start())
                            ]
                        if matches:
                            match_starts = [m.start() for m in matches]
                            if not self._is_unconditional_call(
                                callee.body_lines, callee_pos, match_starts, masked_lines
                            ):
                                continue
                            indirect_match = matches[0]
                            match_str = callee_line_text[
                                indirect_match.start() : indirect_match.end()
                            ].strip()
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

    def audit_await_in_hot_loops(
        self,
        file_path: Path,
        clean_content: str,
        *,
        masked_content: str | None = None,
        funcs: list[FunctionDef] | None = None,
    ) -> list[AuditIssue]:
        """Detect 'await' used directly inside _process or _physics_process (excluding lambdas)."""
        issues: list[AuditIssue] = []
        try:
            rel_file = file_path.relative_to(self.project.root).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        masked = masked_content if masked_content is not None else mask_gdscript_code(clean_content)
        parsed_funcs = (
            funcs if funcs is not None else parse_functions(clean_content, masked_content=masked)
        )
        masked_lines = masked.splitlines()

        for fn in parsed_funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue

            lambda_exclusions = self._compute_lambda_exclusions(fn.body_lines, masked_lines)
            for position, (line_no, line_text) in enumerate(fn.body_lines):
                excl_col = lambda_exclusions.get(position)
                if excl_col == 0:
                    continue
                masked_text = masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""
                matches = list(_AWAIT_REGEX.finditer(masked_text))
                if excl_col is not None:
                    matches = [m for m in matches if m.start() < excl_col]
                if matches:
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.AWAIT_IN_HOT_LOOP,
                            severity=Severity.HIGH,
                            title=f"Await in Hot Loop: {fn.name}()",
                            description=(
                                f"In '{rel_file}' line {line_no}, 'await' inside '{fn.name}()' "
                                "suspends a coroutine while the engine continues invoking the "
                                "callback every frame, causing overlapping executions. Move async "
                                "logic to a separate method or use signals."
                            ),
                            file_path=rel_file,
                            line_number=line_no,
                            context=line_text.strip(),
                        )
                    )

        return issues

    def audit_debug_prints(
        self,
        file_path: Path,
        clean_content: str,
        *,
        masked_content: str | None = None,
        funcs: list[FunctionDef] | None = None,
    ) -> list[AuditIssue]:
        """Detect leftover debug print calls (excluding test scripts and @tool scripts)."""
        if is_test_script(clean_content):
            return []

        masked = masked_content if masked_content is not None else mask_gdscript_code(clean_content)
        if _TOOL_ANNOTATION_REGEX.search(masked) is not None:
            return []

        issues: list[AuditIssue] = []
        try:
            rel_file = file_path.relative_to(self.project.root).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        parsed_funcs = (
            funcs if funcs is not None else parse_functions(clean_content, masked_content=masked)
        )
        clean_lines = clean_content.splitlines()
        masked_lines = masked.splitlines()

        hot_line_info: dict[int, tuple[FunctionDef, int, int | None]] = {}
        for fn in parsed_funcs:
            if fn.name not in ("_process", "_physics_process"):
                continue
            lambda_exclusions = self._compute_lambda_exclusions(fn.body_lines, masked_lines)
            for pos, (line_no, _) in enumerate(fn.body_lines):
                hot_line_info[line_no] = (fn, pos, lambda_exclusions.get(pos))

        for line_no, masked_line in enumerate(masked_lines, start=1):
            valid_matches: list[re.Match[str]] = []
            for m in _DEBUG_PRINT_REGEX.finditer(masked_line):
                prefix = masked_line[: m.start()].rstrip()
                if prefix.endswith(".") or re.search(r"\bfunc$", prefix):
                    continue
                valid_matches.append(m)

            if not valid_matches:
                continue

            severity = Severity.INFO
            hot_fn_name: str | None = None
            info = hot_line_info.get(line_no)
            if info is not None:
                fn, pos, excl_col = info
                non_lambda_starts = [
                    m.start() for m in valid_matches if excl_col is None or m.start() < excl_col
                ]
                if non_lambda_starts and self._is_unconditional_call(
                    fn.body_lines, pos, non_lambda_starts, masked_lines
                ):
                    severity = Severity.MEDIUM
                    hot_fn_name = fn.name

            fn_call = f"{valid_matches[0].group(1)}()"
            line_text = clean_lines[line_no - 1] if line_no - 1 < len(clean_lines) else ""
            if hot_fn_name is not None:
                title = f"Debug Print in Hot Loop: {fn_call} in {hot_fn_name}()"
                description = (
                    f"In '{rel_file}' line {line_no}, unconditional '{fn_call}' inside "
                    f"'{hot_fn_name}()' floods output every frame and degrades performance. "
                    "Remove debug prints or guard them behind a debug flag."
                )
            else:
                title = f"Debug Print Statement: {fn_call}"
                description = (
                    f"In '{rel_file}' line {line_no}, debug call '{fn_call}' found in "
                    "non-test script. Remove leftover debug output or use a logger."
                )

            issues.append(
                AuditIssue(
                    category=IssueCategory.DEBUG_PRINT,
                    severity=severity,
                    title=title,
                    description=description,
                    file_path=rel_file,
                    line_number=line_no,
                    context=line_text.strip(),
                )
            )

        return issues

    def audit_unsafe_node_paths(
        self,
        file_path: Path,
        clean_content: str,
        *,
        masked_content: str | None = None,
    ) -> list[AuditIssue]:
        """Detect fragile get_node()/$ paths with >=3 '../' levels or non-autoload '/root/<X>'."""
        issues: list[AuditIssue] = []
        try:
            rel_file = file_path.relative_to(self.project.root).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        masked = masked_content if masked_content is not None else mask_gdscript_code(clean_content)
        clean_lines = clean_content.splitlines()
        masked_lines = masked.splitlines()
        autoloads = self.project.autoloads

        for line_no, masked_line in enumerate(masked_lines, start=1):
            clean_line = clean_lines[line_no - 1] if line_no - 1 < len(clean_lines) else ""
            extracted_paths: list[str] = []

            for m in _GET_NODE_CALL_REGEX.finditer(masked_line):
                arg_match = _QUOTED_ARG_REGEX.match(clean_line[m.end() :])
                if arg_match:
                    path_val = arg_match.group(1) or arg_match.group(2)
                    if path_val:
                        extracted_paths.append(path_val)

            for m in re.finditer(r"\$", masked_line):
                dollar_match = _DOLLAR_PATH_REGEX.match(clean_line[m.start() :])
                if dollar_match:
                    path_val = (
                        dollar_match.group(1) or dollar_match.group(2) or dollar_match.group(3)
                    )
                    if path_val:
                        extracted_paths.append(path_val)

            seen_on_line: set[str] = set()
            for node_path in extracted_paths:
                if node_path in seen_on_line:
                    continue
                if _DEEP_RELATIVE_PATH_REGEX.search(node_path):
                    seen_on_line.add(node_path)
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.UNSAFE_NODE_PATH,
                            severity=Severity.LOW,
                            title=f"Unsafe Node Path: deep relative traversal '{node_path}'",
                            description=(
                                f"In '{rel_file}' line {line_no}, node path '{node_path}' "
                                "traverses 3 or more parent levels ('../'), making it fragile "
                                "to scene tree refactors. Use signals, @export references, or "
                                "unique node names (%Node)."
                            ),
                            file_path=rel_file,
                            line_number=line_no,
                            context=clean_line.strip(),
                        )
                    )
                elif node_path.startswith("/root/"):
                    remainder = node_path[len("/root/") :]
                    root_child = remainder.split("/", 1)[0]
                    if root_child and root_child not in autoloads:
                        seen_on_line.add(node_path)
                        issues.append(
                            AuditIssue(
                                category=IssueCategory.UNSAFE_NODE_PATH,
                                severity=Severity.LOW,
                                title=f"Unsafe Node Path: hardcoded '/root/{root_child}'",
                                description=(
                                    f"In '{rel_file}' line {line_no}, node path '{node_path}' "
                                    f"references '/root/{root_child}', which is not a registered "
                                    "autoload in project.godot. Use an autoload singleton, "
                                    "signals, or @export references instead."
                                ),
                                file_path=rel_file,
                                line_number=line_no,
                                context=clean_line.strip(),
                            )
                        )

        return issues

    def audit_dead_functions(self) -> list[AuditIssue]:
        """Detect declared functions that are never called or referenced in the project."""
        issues: list[AuditIssue] = []

        # 1. Parse all functions across all .gd files using cached single-pass parse
        declared_funcs: list[tuple[Path, FunctionDef]] = []
        test_files: set[Path] = set()
        for gd_file in self.project.gd_files:
            parsed = self._get_parsed_file(gd_file)
            if parsed is None:
                continue
            cleaned, _masked, funcs = parsed
            if is_test_script(cleaned):
                test_files.add(gd_file)
            for fn in funcs:
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

        # Per-file audits (populates _parsed_cache reused by audit_dead_functions)
        for gd_file in self.project.gd_files:
            parsed = self._get_parsed_file(gd_file)
            if parsed is None:
                continue
            cleaned, masked, funcs = parsed
            issues.extend(
                self.audit_hot_loops(gd_file, cleaned, masked_content=masked, funcs=funcs)
            )
            issues.extend(
                self.audit_await_in_hot_loops(gd_file, cleaned, masked_content=masked, funcs=funcs)
            )
            issues.extend(
                self.audit_debug_prints(gd_file, cleaned, masked_content=masked, funcs=funcs)
            )
            issues.extend(self.audit_unsafe_node_paths(gd_file, cleaned, masked_content=masked))

        # Audit dead functions
        issues.extend(self.audit_dead_functions())

        return issues
