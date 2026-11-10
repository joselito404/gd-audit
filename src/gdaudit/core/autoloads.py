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


def _get_autoload_source_content(entry: AutoloadEntry, project_root: Path) -> str:
    """Retrieve combined source text representing an autoload (script or scene)."""
    file_path = res_to_path(entry.path, project_root)
    if not file_path.is_file():
        return ""

    content = file_path.read_text(encoding="utf-8", errors="replace")

    # If it's a scene (.tscn), inspect both the scene and any attached script
    if file_path.suffix.lower() == ".tscn":
        script_paths = re.findall(
            r'\[ext_resource[^\]]*type="Script"[^\]]*path="([^"]+)"',
            content,
        )
        for s_path in script_paths:
            s_file = res_to_path(s_path, project_root)
            if s_file.is_file():
                content += "\n" + s_file.read_text(encoding="utf-8", errors="replace")

    return content


def _find_canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Return canonical tuple representation of a cycle for deduplication."""
    # cycle is [A, B, C, A] -> elements are [A, B, C]
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
        for name, entry in self.autoloads.items():
            content = _get_autoload_source_content(entry, self.project.root)
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
        issues: list[AuditIssue] = []

        # Read all scripts and scenes excluding the autoload's own files
        project_texts: list[tuple[Path, str]] = []
        for file_path in self.project.gd_files + self.project.tscn_files + self.project.tres_files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                project_texts.append((file_path, text))
            except OSError:
                continue

        for name, entry in self.autoloads.items():
            entry_file = res_to_path(entry.path, self.project.root)
            id_regex = re.compile(rf"\b{re.escape(name)}\b")
            root_regex = re.compile(rf"/root/{re.escape(name)}\b")

            referenced = False
            for path, text in project_texts:
                if path.resolve() == entry_file.resolve():
                    continue

                if id_regex.search(text) or root_regex.search(text):
                    referenced = True
                    break

            if not referenced:
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
        self.build_graph()
        issues: list[AuditIssue] = []
        issues.extend(self.detect_cycles())
        issues.extend(self.detect_unused_autoloads())
        return issues
