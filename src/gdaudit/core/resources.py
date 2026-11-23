"""Validator for Godot .tscn/.tres resources, scene cycles, signals, and orphan assets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.core.gdscript import fast_clean_gdscript_comments
from gdaudit.models import AuditIssue, IssueCategory, Severity
from gdaudit.utils.godot_project import (
    MAX_TEXT_FILE_BYTES,
    is_test_script,
    normalize_res_path,
    path_to_res,
    res_to_path,
)

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import GodotProject

# Declaration of a global class name (``class_name Foo``), run on text without comments.
_CLASS_NAME_REGEX = re.compile(r"^[ \t]*class_name[ \t]+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
# Inheritance declaration (``extends "res://..."`` or ``extends BaseClass``)
_EXTENDS_PATH_REGEX = re.compile(r'^[ \t]*extends[ \t]+["\'](res://[^"\']+)["\']', re.MULTILINE)
_EXTENDS_CLASS_REGEX = re.compile(r"^[ \t]*extends[ \t]+([a-zA-Z_][a-zA-Z0-9_]*)\b", re.MULTILINE)
# Function declarations in GDScript (run on comment-cleaned source)
_FUNC_DECL_REGEX = re.compile(
    r"^[ \t]*(?:static[ \t]+)?func[ \t]+([a-zA-Z_][a-zA-Z0-9_]*)[ \t]*\(",
    re.MULTILINE,
)

# Matches [ext_resource type="..." path="..." id="..."] in Godot 4 or Godot 3
_EXT_RESOURCE_RE = re.compile(r"^\[ext_resource\s+([^\]]+)\]")
_EXT_ATTR_RE = re.compile(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|([^\s]+))')
_USED_EXT_RESOURCE_RE = re.compile(r'ExtResource\(\s*["\']?([^"\'\)]+)["\']?\s*\)')

# Matches [node ... instance=ExtResource("...")] (only scene instancing, never @export properties)
_NODE_HEADER_RE = re.compile(r"^\[node\s+([^\]]+)\]")
_NODE_INSTANCE_RE = re.compile(
    r'^\[node\b[^\]]*\binstance\s*=\s*ExtResource\(\s*["\']?([^"\'\)]+)["\']?\s*\)',
    re.MULTILINE,
)
_SCRIPT_PROP_RE = re.compile(r'^script\s*=\s*ExtResource\(\s*["\']?([^"\'\)]+)["\']?\s*\)')
_CONNECTION_HEADER_RE = re.compile(r"^\[connection\s+([^\]]+)\]")

# Matches res:// string literals passed to GDScript resource/file loaders
_GDSCRIPT_LOAD_PATH_RE = re.compile(
    r"\b(?P<call>preload|load|ResourceLoader\.load"
    r"|FileAccess\.open_encrypted_with_pass|FileAccess\.open_encrypted"
    r"|FileAccess\.open_compressed|FileAccess\.open"
    r"|FileAccess\.get_file_as_string|FileAccess\.get_file_as_bytes)"
    r"\s*\(\s*(?P<quote>[\"'])(?P<path>res://[^\"'\r\n]+)(?P=quote)"
    r"(?P<after>\s*(?:\+|%|\.format\b))?"
)
_FILEACCESS_WRITE_RE = re.compile(r"\bFileAccess\.(?:WRITE|WRITE_READ)\b")

# Common built-in Godot Node/Object methods frequently targeted by .tscn signal connections
_GODOT_BUILTIN_SIGNAL_METHODS: frozenset[str] = frozenset(
    {
        "queue_free",
        "free",
        "show",
        "hide",
        "play",
        "stop",
        "start",
        "pause",
        "restart",
        "clear",
        "reset",
        "update",
        "queue_redraw",
        "set_process",
        "set_physics_process",
        "set_process_input",
        "set_process_unhandled_input",
        "set_visible",
        "set_disabled",
        "set_deferred",
        "call_deferred",
        "emit_signal",
        "grab_focus",
        "release_focus",
        "popup",
        "popup_centered",
        "add_child",
        "remove_child",
        "reparent",
        "set_text",
        "set_value",
        "set_pressed",
        "set_pressed_no_signal",
        "set_paused",
        "set_monitoring",
        "set_monitorable",
        "set_emitting",
        "set_playing",
        "set_stream_paused",
        "set_modulate",
        "set_self_modulate",
        "set_position",
        "set_rotation",
        "set_scale",
        "translate",
        "rotate",
        "look_at",
        "move_to_front",
        "print_tree",
        "print_tree_pretty",
    }
)


@dataclass
class ExtResourceDecl:
    """Represents an [ext_resource] declaration in a scene or resource file."""

    res_id: str
    res_type: str
    path: str
    uid: str | None = None
    line_number: int = 1


def _format_size(num_bytes: int) -> str:
    """Format byte counts into human-readable strings."""
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _parse_tag_attrs(raw_attrs: str) -> dict[str, str]:
    """Parse key=value attributes inside a Godot .tscn/.tres section header."""
    attrs: dict[str, str] = {}
    for attr_match in _EXT_ATTR_RE.finditer(raw_attrs):
        key = attr_match.group(1)
        val = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
        attrs[key] = val
    return attrs


def _find_canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Return canonical tuple representation of a cycle for deduplication."""
    nodes = cycle[:-1]
    n = len(nodes)
    if n == 0:
        return ()
    rotations = [nodes[i:] + nodes[:i] for i in range(n)]
    return tuple(min(rotations))


class ResourceAnalyzer:
    """Validates external resources, scene graphs, signal connections, and orphan assets."""

    def __init__(self, project: GodotProject) -> None:
        self.project: GodotProject = project
        self._class_name_map: dict[str, Path] | None = None
        self._script_funcs_cache: dict[Path, set[str]] = {}

    def _read_text_safe(self, file_path: Path) -> str | None:
        """Retrieve a file's text from cache or disk while respecting MAX_TEXT_FILE_BYTES."""
        content = self.project.file_texts.get(file_path)
        if content is not None:
            return content
        try:
            if file_path.stat().st_size > MAX_TEXT_FILE_BYTES:
                return None
            return file_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None

    def parse_ext_resources(self, file_path: Path) -> tuple[list[ExtResourceDecl], str]:
        """Parse all [ext_resource] lines from a .tscn or .tres file."""
        content = self._read_text_safe(file_path)
        if content is None:
            return [], ""

        ext_resources: list[ExtResourceDecl] = []
        body_lines: list[str] = []

        for idx, line in enumerate(content.splitlines(), start=1):
            match = _EXT_RESOURCE_RE.match(line.strip())
            if match:
                attrs = _parse_tag_attrs(match.group(1))
                res_id = attrs.get("id", "")
                res_path = attrs.get("path", "")
                res_type = attrs.get("type", "Unknown")
                res_uid = attrs.get("uid")

                if not res_path and res_uid:
                    resolved = self.project.resolve_uid(res_uid)
                    res_path = resolved if resolved is not None else res_uid

                if res_id and res_path:
                    ext_resources.append(
                        ExtResourceDecl(
                            res_id=res_id,
                            res_type=res_type,
                            path=res_path,
                            uid=res_uid,
                            line_number=idx,
                        )
                    )
            else:
                body_lines.append(line)

        return ext_resources, "\n".join(body_lines)

    def _res_exists(self, res_uri: str) -> bool:
        """Check whether a res:// URI exists in project index or on disk."""
        norm_path = normalize_res_path(res_uri)
        return (
            res_uri in self.project.known_res
            or norm_path in self.project.known_res
            or self.project.exists_on_disk(res_uri)
        )

    def audit_scene_resources(self) -> list[AuditIssue]:
        """Audit all .tscn and .tres files for broken and unused ext_resources."""
        issues: list[AuditIssue] = []
        files_to_check = self.project.tscn_files + self.project.tres_files

        for file_path in files_to_check:
            rel_file = file_path.relative_to(self.project.root).as_posix()
            ext_resources, body = self.parse_ext_resources(file_path)

            # Single-pass regex to extract all ExtResource IDs used in the scene body
            used_ids = set(_USED_EXT_RESOURCE_RE.findall(body))

            for decl in ext_resources:
                # 1. Check for Broken Resource in memory against known_res (O(1), zero disk calls)
                # Unresolved uid:// references (without a local .uid sidecar) skip the disk check
                # so we do not emit false positives for addon/engine UIDs.
                if not decl.path.startswith("uid://") and not self._res_exists(decl.path):
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.BROKEN_RESOURCE,
                            severity=Severity.HIGH,
                            title=f"Broken Resource Reference: {decl.path}",
                            description=(
                                f"In '{rel_file}' (line {decl.line_number}), external resource "
                                f"'{decl.path}' does not exist on disk."
                            ),
                            file_path=rel_file,
                            line_number=decl.line_number,
                            context=f'id="{decl.res_id}" path="{decl.path}"',
                        )
                    )
                    continue

                # 2. Check for Declared but Unused Resource in O(1)
                if decl.res_id not in used_ids:
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.UNUSED_RESOURCE,
                            severity=Severity.MEDIUM,
                            title=f"Unused Declared Resource: {decl.path}",
                            description=(
                                f"Resource '{decl.path}' (id='{decl.res_id}') in '{rel_file}' "
                                f"(line {decl.line_number}) is never used."
                            ),
                            file_path=rel_file,
                            line_number=decl.line_number,
                            context=f'id="{decl.res_id}" path="{decl.path}"',
                        )
                    )

        return issues

    def audit_scene_cycles(self) -> list[AuditIssue]:
        """Detect circular scene instancing cycles (A.tscn -> B.tscn -> A.tscn)."""
        scene_graph: dict[str, set[str]] = {}
        res_to_rel: dict[str, str] = {}

        for tscn_path in self.project.tscn_files:
            src_res = normalize_res_path(path_to_res(tscn_path, self.project.root))
            res_to_rel[src_res] = tscn_path.relative_to(self.project.root).as_posix()
            ext_resources, body = self.parse_ext_resources(tscn_path)

            id_to_scene = {
                decl.res_id: normalize_res_path(decl.path)
                for decl in ext_resources
                if decl.path.lower().endswith(".tscn")
            }
            targets: set[str] = set()
            for inst_id in _NODE_INSTANCE_RE.findall(body):
                target_res = id_to_scene.get(inst_id)
                if target_res:
                    targets.add(target_res)
            scene_graph[src_res] = targets

        issues: list[AuditIssue] = []
        visited: set[str] = set()
        rec_stack: list[str] = []
        reported_cycles: set[tuple[str, ...]] = set()

        def dfs(current: str) -> None:
            visited.add(current)
            rec_stack.append(current)

            for neighbor in sorted(scene_graph.get(current, set())):
                if neighbor in rec_stack:
                    idx = rec_stack.index(neighbor)
                    cycle_path = rec_stack[idx:] + [neighbor]
                    canonical = _find_canonical_cycle(cycle_path)
                    if canonical and canonical not in reported_cycles:
                        reported_cycles.add(canonical)
                        cycle_str = " -> ".join(cycle_path)
                        rel_file = res_to_rel.get(
                            neighbor, neighbor.removeprefix("res://").lstrip("/")
                        )
                        issues.append(
                            AuditIssue(
                                category=IssueCategory.SCENE_CYCLE,
                                severity=Severity.HIGH,
                                title=f"Circular Scene Instancing: {canonical[0]}",
                                description=(
                                    f"Circular scene instancing cycle detected: {cycle_str}. "
                                    "Instancing a scene that directly or indirectly instances "
                                    "itself causes infinite recursion in Godot."
                                ),
                                file_path=rel_file,
                                context=f"Cycle: {cycle_str}",
                            )
                        )
                elif neighbor not in visited and neighbor in scene_graph:
                    dfs(neighbor)

            rec_stack.pop()

        for node in sorted(scene_graph.keys()):
            if node not in visited:
                dfs(node)

        return issues

    def audit_broken_resource_paths(self) -> list[AuditIssue]:
        """Detect broken res:// literals in GDScript loaders and project.godot."""
        issues: list[AuditIssue] = []

        # 1. Check GDScript load/preload/ResourceLoader.load/FileAccess literals
        for gd_path in self.project.gd_files:
            content = self._read_text_safe(gd_path)
            if content is None:
                continue
            cleaned = fast_clean_gdscript_comments(content)
            if is_test_script(cleaned):
                continue

            rel_file = gd_path.relative_to(self.project.root).as_posix()
            for line_num, line in enumerate(cleaned.splitlines(), start=1):
                for match in _GDSCRIPT_LOAD_PATH_RE.finditer(line):
                    res_uri = match.group("path")
                    if match.group("after"):
                        continue
                    if res_uri.endswith("/") or "%" in res_uri or "{" in res_uri:
                        continue
                    call_name = match.group("call")
                    if call_name.startswith("FileAccess.open") and _FILEACCESS_WRITE_RE.search(
                        line[match.end() :]
                    ):
                        continue

                    if not self._res_exists(res_uri):
                        issues.append(
                            AuditIssue(
                                category=IssueCategory.BROKEN_RESOURCE_PATH,
                                severity=Severity.HIGH,
                                title=f"Broken Resource Path: {res_uri}",
                                description=(
                                    f"In '{rel_file}' (line {line_num}), "
                                    f"'{call_name}(\"{res_uri}\")' "
                                    "references a resource that does not exist on disk."
                                ),
                                file_path=rel_file,
                                line_number=line_num,
                                context=line.strip(),
                            )
                        )

        # 2. Check configured res:// paths in project.godot
        proj_text = self.project.file_texts.get(self.project.project_godot_path, "")
        proj_lines = proj_text.splitlines()

        def _find_proj_line(needle: str) -> int | None:
            for idx, line in enumerate(proj_lines, start=1):
                if needle in line:
                    return idx
            return None

        proj_paths: list[tuple[str, str]] = []
        if self.project.main_scene:
            proj_paths.append(("run/main_scene", self.project.main_scene.strip()))
        app_sec = self.project.raw_sections.get("application", {})
        if "config/icon" in app_sec:
            proj_paths.append(("config/icon", app_sec["config/icon"].strip()))
        for entry in self.project.autoloads.values():
            if entry.path:
                proj_paths.append((f"autoload/{entry.name}", entry.path.strip()))

        for key_label, res_uri in proj_paths:
            if not res_uri.startswith("res://") or res_uri.endswith("/"):
                continue
            if not self._res_exists(res_uri):
                proj_line_num = _find_proj_line(res_uri)
                issues.append(
                    AuditIssue(
                        category=IssueCategory.BROKEN_RESOURCE_PATH,
                        severity=Severity.HIGH,
                        title=f"Broken Project Resource Path: {res_uri}",
                        description=(
                            f"In 'project.godot' ({key_label}), resource path '{res_uri}' "
                            "does not exist on disk."
                        ),
                        file_path="project.godot",
                        line_number=proj_line_num,
                        context=f"{key_label}={res_uri}",
                    )
                )

        return issues

    def _get_class_name_map(self) -> dict[str, Path]:
        """Build a lazy map of GDScript class_name -> file path."""
        if self._class_name_map is None:
            mapping: dict[str, Path] = {}
            for gd_path in self.project.gd_files:
                content = self._read_text_safe(gd_path)
                if content is None:
                    continue
                cleaned = fast_clean_gdscript_comments(content)
                match = _CLASS_NAME_REGEX.search(cleaned)
                if match:
                    mapping[match.group(1)] = gd_path
            self._class_name_map = mapping
        return self._class_name_map

    def _get_script_methods(self, script_path: Path, _visited: set[Path] | None = None) -> set[str]:
        """Return all function names declared in a .gd script and its local base scripts."""
        if script_path in self._script_funcs_cache:
            return self._script_funcs_cache[script_path]

        visited = set() if _visited is None else _visited
        if script_path in visited:
            return set()
        visited.add(script_path)

        content = self._read_text_safe(script_path)
        if content is None:
            return set()

        cleaned = fast_clean_gdscript_comments(content)
        methods = set(_FUNC_DECL_REGEX.findall(cleaned))

        # Follow local inheritance (extends "res://..." or extends LocalClassName)
        parent_path: Path | None = None
        ext_path_match = _EXTENDS_PATH_REGEX.search(cleaned)
        if ext_path_match:
            try:
                candidate = res_to_path(ext_path_match.group(1), self.project.root)
                if candidate.is_file():
                    parent_path = candidate
            except ValueError:
                pass
        else:
            ext_cls_match = _EXTENDS_CLASS_REGEX.search(cleaned)
            if ext_cls_match:
                parent_path = self._get_class_name_map().get(ext_cls_match.group(1))

        if parent_path is not None and parent_path not in visited:
            methods.update(self._get_script_methods(parent_path, visited))

        self._script_funcs_cache[script_path] = methods
        return methods

    def audit_broken_signal_connections(self) -> list[AuditIssue]:
        """Validate .tscn [connection] target methods against locally attached scripts."""
        issues: list[AuditIssue] = []

        for tscn_path in self.project.tscn_files:
            content = self._read_text_safe(tscn_path)
            if content is None:
                continue

            ext_resources, _ = self.parse_ext_resources(tscn_path)
            script_ext_ids = {
                decl.res_id: decl.path
                for decl in ext_resources
                if (decl.res_type == "Script" or decl.path.lower().endswith(".gd"))
                and decl.path.startswith("res://")
            }
            if not script_ext_ids:
                continue

            node_scripts: dict[str, str] = {}
            connections: list[tuple[int, str, str, str]] = []
            current_node_path: str | None = None

            for idx, raw_line in enumerate(content.splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue

                if line.startswith("["):
                    node_match = _NODE_HEADER_RE.match(line)
                    if node_match:
                        attrs = _parse_tag_attrs(node_match.group(1))
                        node_name = attrs.get("name", "")
                        parent = attrs.get("parent")
                        if parent is None:
                            current_node_path = "."
                        elif parent == ".":
                            current_node_path = node_name
                        else:
                            current_node_path = f"{parent}/{node_name}"
                        continue

                    current_node_path = None
                    conn_match = _CONNECTION_HEADER_RE.match(line)
                    if conn_match:
                        attrs = _parse_tag_attrs(conn_match.group(1))
                        signal_name = attrs.get("signal", "")
                        to_path = attrs.get("to", "")
                        method_name = attrs.get("method", "")
                        if to_path and method_name:
                            connections.append((idx, signal_name, to_path, method_name))
                    continue

                if current_node_path is not None:
                    script_match = _SCRIPT_PROP_RE.match(line)
                    if script_match:
                        res_id = script_match.group(1)
                        if res_id in script_ext_ids:
                            node_scripts[current_node_path] = script_ext_ids[res_id]

            if not connections:
                continue

            rel_file = tscn_path.relative_to(self.project.root).as_posix()
            for conn_line, signal_name, to_path, method_name in connections:
                if method_name in _GODOT_BUILTIN_SIGNAL_METHODS:
                    continue
                target_script_res = node_scripts.get(to_path)
                if not target_script_res:
                    continue
                try:
                    script_file = res_to_path(target_script_res, self.project.root)
                except ValueError:
                    continue
                if not script_file.is_file():
                    continue

                declared_methods = self._get_script_methods(script_file)
                if method_name not in declared_methods:
                    issues.append(
                        AuditIssue(
                            category=IssueCategory.BROKEN_SIGNAL_CONNECTION,
                            severity=Severity.HIGH,
                            title=f"Broken Signal Connection: {method_name}",
                            description=(
                                f"In '{rel_file}' (line {conn_line}), signal '{signal_name}' "
                                f"connects to method '{method_name}' on node '{to_path}' "
                                f"({target_script_res}), which is not defined in that script."
                            ),
                            file_path=rel_file,
                            line_number=conn_line,
                            context=f'signal="{signal_name}" to="{to_path}" method="{method_name}"',
                        )
                    )

        return issues

    def _class_name_is_referenced(
        self, file_path: Path, *, content: str | None = None, cleaned: str | None = None
    ) -> bool:
        """Check whether a script's ``class_name`` is used by name in the project.

        Scripts with ``class_name Foo`` are used as ``Foo.new()`` or ``var x: Foo``, never by path.
        ``word_counts`` counts the identifier in every indexed file, the script itself included,
        so it is referenced when the project holds more occurrences than its own text does.
        """
        raw_content = content if content is not None else self.project.file_texts.get(file_path)
        if raw_content is None:
            return False
        clean_src = cleaned if cleaned is not None else fast_clean_gdscript_comments(raw_content)
        match = _CLASS_NAME_REGEX.search(clean_src)
        if match is None:
            return False
        name = match.group(1)
        own_occurrences = len(re.findall(rf"\b{re.escape(name)}\b", raw_content))
        return self.project.word_counts.get(name, 0) > own_occurrences

    def audit_orphan_assets(self) -> list[AuditIssue]:
        """Find candidate asset files on disk that are never referenced anywhere."""
        issues: list[AuditIssue] = []

        # Pre-normalize main scene and autoload URIs once for consistent O(1) comparison
        norm_main_scene = (
            normalize_res_path(self.project.main_scene) if self.project.main_scene else None
        )
        norm_autoload_paths = {
            normalize_res_path(entry.path)
            for entry in self.project.autoloads.values()
            if entry.path
        }

        # Check candidate files: scenes, resources, audio, textures, shaders, models, etc.
        candidates = self.project.asset_files + self.project.tscn_files + self.project.tres_files
        candidates.extend(self.project.gd_files)

        for file_path in candidates:
            # Skip project.godot or special engine files
            if file_path.name in ("project.godot", "export_presets.cfg"):
                continue

            # Clean comments once per .gd file for both test-script and class_name checks
            if file_path.suffix.lower() == ".gd":
                gd_content = self._read_text_safe(file_path)
                if gd_content is not None:
                    gd_cleaned = fast_clean_gdscript_comments(gd_content)
                    if is_test_script(gd_cleaned):
                        continue
                    if self._class_name_is_referenced(
                        file_path, content=gd_content, cleaned=gd_cleaned
                    ):
                        continue

            res_uri = path_to_res(file_path, self.project.root)
            norm_res_uri = normalize_res_path(res_uri)
            rel_file = file_path.relative_to(self.project.root).as_posix()

            # Skip main scene or autoloads directly
            if norm_main_scene and norm_res_uri == norm_main_scene:
                continue
            if norm_res_uri in norm_autoload_paths:
                continue

            # Evaluate against exact references and dynamic path prefixes, in memory
            if not self.project.is_res_referenced(norm_res_uri):
                size_str = ""
                try:
                    size = file_path.stat().st_size
                    size_str = f" ({_format_size(size)})"
                except OSError:
                    pass

                issues.append(
                    AuditIssue(
                        category=IssueCategory.ORPHAN_ASSET,
                        severity=Severity.LOW,
                        title=f"Orphan Asset Candidate: {rel_file}",
                        description=(
                            f"Asset '{rel_file}'{size_str} is present on disk but "
                            "is not referenced by any scene, resource, or script."
                        ),
                        file_path=rel_file,
                        context=res_uri,
                    )
                )

        return issues

    def analyze(self, check_orphans: bool = True) -> list[AuditIssue]:
        """Execute full resource, scene, signal, and asset audit."""
        issues: list[AuditIssue] = []
        issues.extend(self.audit_scene_resources())
        issues.extend(self.audit_scene_cycles())
        issues.extend(self.audit_broken_resource_paths())
        issues.extend(self.audit_broken_signal_connections())
        if check_orphans:
            issues.extend(self.audit_orphan_assets())
        return issues
