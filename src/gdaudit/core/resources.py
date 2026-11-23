"""Parser and validator for Godot .tscn and .tres files, broken resources, and orphan assets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.core.gdscript import fast_clean_gdscript_comments
from gdaudit.models import AuditIssue, IssueCategory, Severity
from gdaudit.utils.godot_project import (
    is_test_script,
    normalize_res_path,
    path_to_res,
)

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import GodotProject

# Declaration of a global class name (``class_name Foo``), run on text without comments.
_CLASS_NAME_REGEX = re.compile(r"^[ \t]*class_name[ \t]+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
# Matches [ext_resource type="..." path="..." id="..."] in Godot 4 or Godot 3
_EXT_RESOURCE_RE = re.compile(r"^\[ext_resource\s+([^\]]+)\]")
_EXT_ATTR_RE = re.compile(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|([^\s]+))')
_USED_EXT_RESOURCE_RE = re.compile(r'ExtResource\(\s*["\']?([^"\'\)]+)["\']?\s*\)')


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


class ResourceAnalyzer:
    """Validates external resource integrity and identifies dangling or orphan assets."""

    def __init__(self, project: GodotProject) -> None:
        self.project: GodotProject = project

    def parse_ext_resources(self, file_path: Path) -> tuple[list[ExtResourceDecl], str]:
        """Parse all [ext_resource] lines from a .tscn or .tres file."""
        content = self.project.file_texts.get(file_path)
        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return [], ""

        ext_resources: list[ExtResourceDecl] = []
        body_lines: list[str] = []

        for idx, line in enumerate(content.splitlines(), start=1):
            match = _EXT_RESOURCE_RE.match(line.strip())
            if match:
                raw_attrs = match.group(1)
                attrs: dict[str, str] = {}
                for attr_match in _EXT_ATTR_RE.finditer(raw_attrs):
                    key = attr_match.group(1)
                    val = (
                        attr_match.group(2)
                        if attr_match.group(2) is not None
                        else attr_match.group(3)
                    )
                    attrs[key] = val

                res_id = attrs.get("id", "")
                res_path = attrs.get("path", "")
                res_type = attrs.get("type", "Unknown")
                res_uid = attrs.get("uid")

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
                norm_path = normalize_res_path(decl.path)
                if (
                    decl.path not in self.project.known_res
                    and norm_path not in self.project.known_res
                    # Files in ignored folders (e.g. addons/) are not indexed but do exist
                    and not self.project.exists_on_disk(decl.path)
                ):
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

    def find_all_referenced_resources(self) -> set[str]:
        """Collect all 'res://' URIs referenced across the entire project (cached in project)."""
        return self.project.referenced_res

    def _read_gd_text(self, file_path: Path) -> str | None:
        """Retrieve a .gd file's text from cache or disk."""
        content = self.project.file_texts.get(file_path)
        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return None
        return content

    def _is_test_script_file(self, file_path: Path, *, cleaned: str | None = None) -> bool:
        """Check whether a .gd file extends GutTest / GdUnitTestSuite."""
        if cleaned is None:
            content = self._read_gd_text(file_path)
            if content is None:
                return False
            cleaned = fast_clean_gdscript_comments(content)
        return is_test_script(cleaned)

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
                gd_content = self._read_gd_text(file_path)
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
        """Execute full resource and asset audit."""
        issues: list[AuditIssue] = []
        issues.extend(self.audit_scene_resources())
        if check_orphans:
            issues.extend(self.audit_orphan_assets())
        return issues
