"""Detector for Godot Autoload singletons, circular dependencies, and unused singletons."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.models import AuditIssue, IssueCategory, Severity
from gdaudit.utils.godot_project import res_to_path

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import AutoloadEntry, GodotProject


def _strip_comments(source_code: str) -> str:
    """Strip GDScript single-line comments (#...) while preserving strings."""
    lines: list[str] = []
    for line in source_code.splitlines():
        in_single_quote = False
        in_double_quote = False
        escaped = False
        comment_start = -1

        for idx, char in enumerate(line):
            if escaped:
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == "#" and not in_single_quote and not in_double_quote:
                comment_start = idx
                break

        if comment_start != -1:
            lines.append(line[:comment_start])
        else:
            lines.append(line)

    return "\n".join(lines)


def _extract_script_paths_from_tscn(content: str) -> list[str]:
    """Extract script resource paths from .tscn, tolerant to attribute order."""
    script_paths: list[str] = []
    ext_tags = re.findall(r"\[ext_resource\s+([^\]]+)\]", content)
    for tag in ext_tags:
        if 'type="Script"' in tag or "type='Script'" in tag:
            m = re.search(r'path=["\']([^"\']+)["\']', tag)
            if m:
                script_paths.append(m.group(1))
    return script_paths


def _get_autoload_source_content(entry: AutoloadEntry, project_or_root: GodotProject | Path) -> str:
    """Retrieve combined source text representing an autoload (script or scene)."""
    if isinstance(project_or_root, Path):
        project_root: Path = project_or_root
        file_texts: dict[Path, str] = {}
    else:
        project_root = project_or_root.root
        file_texts = project_or_root.file_texts

    try:
        file_path = res_to_path(entry.path, project_root)
    except ValueError:
        return ""

    content = file_texts.get(file_path)
    if content is None:
        if not file_path.is_file():
            return ""
        try:
            content = file_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return ""

    # If it's a scene (.tscn), inspect both the scene and any attached script
    if file_path.suffix.lower() == ".tscn":
        script_paths = _extract_script_paths_from_tscn(content)
        for s_path in script_paths:
            try:
                s_file = res_to_path(s_path, project_root)
                s_text = file_texts.get(s_file)
                if s_text is None and s_file.is_file():
                    s_text = s_file.read_text(encoding="utf-8-sig", errors="replace")
                if s_text:
                    content += "\n" + s_text
            except (ValueError, OSError):
                continue

    return content


def _find_canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Return canonical tuple representation of a cycle for deduplication."""
    nodes = cycle[:-1]
    n = len(nodes)
    if n == 0:
        return ()
    rotations = [nodes[i:] + nodes[:i] for i in range(n)]
    return tuple(min(rotations))


class AutoloadAnalyzer:
    """Analyzes autoload dependency graphs and flags circular deadlocks and unused entries."""

    def __init__(self, project: GodotProject) -> None:
        self.project: GodotProject = project
        self.autoloads: dict[str, AutoloadEntry] = project.autoloads
        self.dependency_graph: dict[str, set[str]] = {name: set() for name in self.autoloads}

    def build_graph(self) -> dict[str, set[str]]:
        """Construct directed dependency graph: A -> B means Autoload A references Autoload B."""
        if not self.autoloads:
            return self.dependency_graph

        for name, entry in self.autoloads.items():
            content = _get_autoload_source_content(entry, self.project)
            clean_content = _strip_comments(content)

            for target_name, target_entry in self.autoloads.items():
                if name == target_name:
                    continue

                # Patterns to detect reference to target autoload:
                # 1. Global singleton identifier: `Target.` or `: Target`
                id_pattern = rf"\b{re.escape(target_name)}\b"
                # 2. Scene tree path: `/root/Target` or `$"root/Target"`
                root_pattern = rf"/root/{re.escape(target_name)}\b"
                # 3. Direct res:// resource reference
                res_pattern = re.escape(target_entry.path)

                if (
                    re.search(id_pattern, clean_content)
                    or re.search(root_pattern, clean_content)
                    or (target_entry.path and re.search(res_pattern, clean_content))
                ):
                    self.dependency_graph[name].add(target_name)

        return self.dependency_graph

    def detect_cycles(self) -> list[AuditIssue]:
        """Detect circular dependency cycles in autoloads via DFS."""
        if not self.autoloads:
            return []

        issues: list[AuditIssue] = []
        visited: set[str] = set()
        rec_stack: list[str] = []
        reported_cycles: set[tuple[str, ...]] = set()

        def dfs(current: str) -> None:
            visited.add(current)
            rec_stack.append(current)

            for neighbor in sorted(self.dependency_graph.get(current, set())):
                if neighbor in rec_stack:
                    # Cycle found!
                    idx = rec_stack.index(neighbor)
                    cycle_path = rec_stack[idx:] + [neighbor]
                    canonical = _find_canonical_cycle(cycle_path)

                    if canonical not in reported_cycles:
                        reported_cycles.add(canonical)
                        cycle_str = " -> ".join(cycle_path)
                        first_entry = self.autoloads[neighbor]

                        issues.append(
                            AuditIssue(
                                category=IssueCategory.AUTOLOAD_CYCLE,
                                severity=Severity.HIGH,
                                title=f"Circular Autoload Dependency: {canonical[0]}",
                                description=(
                                    f"Circular initialization cycle detected: {cycle_str}. "
                                    "Godot loads autoloads sequentially; mutual references "
                                    "cause null references or startup crashes."
                                ),
                                file_path=first_entry.path,
                                context=f"Cycle: {cycle_str}",
                            )
                        )
                elif neighbor not in visited:
                    dfs(neighbor)

            rec_stack.pop()

        for node in sorted(self.autoloads.keys()):
            if node not in visited:
                dfs(node)

        return issues

    def detect_unused_autoloads(self) -> list[AuditIssue]:
        """Detect singletons in project.godot never referenced in the project."""
        if not self.autoloads:
            return []

        issues: list[AuditIssue] = []
        proj_godot_text = self.project.file_texts.get(self.project.project_godot_path, "")

        for name, entry in self.autoloads.items():
            try:
                entry_file = res_to_path(entry.path, self.project.root)
            except ValueError:
                continue

            # Count occurrences in project.godot
            godot_count = len(re.findall(rf"\b{re.escape(name)}\b", proj_godot_text))

            # Count occurrences in autoload's own file(s)
            own_file_text = self.project.file_texts.get(entry_file, "")
            own_count = len(re.findall(rf"\b{re.escape(name)}\b", own_file_text))

            if entry_file.suffix.lower() == ".tscn":
                for s_path in _extract_script_paths_from_tscn(own_file_text):
                    try:
                        s_file = res_to_path(s_path, self.project.root)
                        s_text = self.project.file_texts.get(s_file, "")
                        own_count += len(re.findall(rf"\b{re.escape(name)}\b", s_text))
                    except (ValueError, OSError):
                        pass

            total_count = self.project.word_counts.get(name, 0)
            external_refs = total_count - godot_count - own_count

            if external_refs <= 0:
                issues.append(
                    AuditIssue(
                        category=IssueCategory.UNUSED_AUTOLOAD,
                        severity=Severity.INFO,
                        title=f"Unused Autoload Singleton: {name}",
                        description=(
                            f"Autoload '{name}' ({entry.path}) is registered in project.godot "
                            "but is never referenced in any script or scene."
                        ),
                        file_path="project.godot",
                        context=f'{name}="{entry.path}"',
                    )
                )

        return issues

    def analyze(self) -> list[AuditIssue]:
        """Run all autoload audits and return aggregated issues."""
        if not self.autoloads:
            return []

        self.build_graph()
        issues: list[AuditIssue] = []
        issues.extend(self.detect_cycles())
        issues.extend(self.detect_unused_autoloads())
        return issues
