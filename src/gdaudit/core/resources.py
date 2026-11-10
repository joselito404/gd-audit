"""Parser and validator for Godot .tscn and .tres files, broken resources, and orphan assets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gdaudit.models import AuditIssue, IssueCategory, Severity
from gdaudit.utils.godot_project import path_to_res, res_to_path

if TYPE_CHECKING:
    from gdaudit.utils.godot_project import GodotProject


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
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes //= 1024
    return f"{num_bytes} GB"


class ResourceAnalyzer:
    """Validates external resource integrity and identifies dangling or orphan assets."""

    def __init__(self, project: GodotProject) -> None:
        self.project: GodotProject = project

    def parse_ext_resources(self, file_path: Path) -> tuple[list[ExtResourceDecl], str]:
        """Parse all [ext_resource] lines from a .tscn or .tres file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], ""

        ext_resources: list[ExtResourceDecl] = []
        body_lines: list[str] = []

        # Matches [ext_resource type="..." path="..." id="..."] in Godot 4 or Godot 3
        ext_re = re.compile(r"^\[ext_resource\s+([^\]]+)\]")
        attr_re = re.compile(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|([^\s]+))')

        for idx, line in enumerate(content.splitlines(), start=1):
            match = ext_re.match(line.strip())
            if match:
                raw_attrs = match.group(1)
                attrs: dict[str, str] = {}
                for attr_match in attr_re.finditer(raw_attrs):
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

            for decl in ext_resources:
                # 1. Check for Broken Resource
                target_path = res_to_path(decl.path, self.project.root)
                if not target_path.exists():
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

                # 2. Check for Declared but Unused Resource
                # Godot 4: ExtResource("id") or ExtResource('id')
                # Godot 3: ExtResource( 1 ) or ExtResource(1)
                # Or just ExtResource( ... id ... )
                used_pattern = rf'ExtResource\(\s*["\']?{re.escape(decl.res_id)}["\']?\s*\)'
                if not re.search(used_pattern, body):
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
        """Collect all 'res://' URIs referenced across the entire project."""
        referenced: set[str] = set()

        # 1. Main scene from project.godot
        if self.project.main_scene:
            referenced.add(self.project.main_scene.strip())

        # 2. Autoload paths
        for entry in self.project.autoloads.values():
            if entry.path:
                referenced.add(entry.path.strip())

        # 3. Application icon or config paths in project.godot
        app_sec = self.project.raw_sections.get("application", {})
        if "config/icon" in app_sec:
            referenced.add(app_sec["config/icon"].strip())

        # 4. Search all .tscn, .tres, and .gd files for any res:// string literals or ext_resources
        res_regex = re.compile(r"res://[a-zA-Z0-9_./\-+]+")

        all_text_files = (
            self.project.gd_files
            + self.project.tscn_files
            + self.project.tres_files
            + [self.project.project_godot_path]
        )

        for f in all_text_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                for match in res_regex.findall(text):
                    # Clean trailing punctuation from matches
                    clean_res = match.rstrip("\"';,)]}")
                    referenced.add(clean_res)
            except OSError:
                continue

        return referenced

    def audit_orphan_assets(self) -> list[AuditIssue]:
        """Find candidate asset files on disk that are never referenced anywhere."""
        issues: list[AuditIssue] = []
        referenced_res = self.find_all_referenced_resources()

        # Check candidate files: scenes, resources, audio, textures, shaders, models, etc.
        candidates = self.project.asset_files + self.project.tscn_files + self.project.tres_files

        # Also check GDScript files that are not autoloads or main scene
        candidates.extend(self.project.gd_files)

        for file_path in candidates:
            # Skip project.godot or special engine files
            if file_path.name in ("project.godot", "export_presets.cfg"):
                continue

            res_uri = path_to_res(file_path, self.project.root)
            rel_file = file_path.relative_to(self.project.root).as_posix()

            # Skip main scene or autoloads directly
            if res_uri == self.project.main_scene:
                continue
            if any(entry.path == res_uri for entry in self.project.autoloads.values()):
                continue

            # If not in the referenced set, flag as orphan asset
            if res_uri not in referenced_res:
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
