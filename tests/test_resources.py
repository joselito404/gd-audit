"""Unit tests for scene and resource validation (broken, unused, and orphan assets)."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.resources import ResourceAnalyzer
from gdaudit.models import IssueCategory, Severity
from gdaudit.utils.godot_project import GodotProject


def test_broken_resource_detection(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    # Scene references missing.gd which does not exist
    scene = tmp_path / "test.tscn"
    scene.write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = ResourceAnalyzer(project)
    issues = analyzer.audit_scene_resources()

    broken = [i for i in issues if i.category == IssueCategory.BROKEN_RESOURCE]
    assert len(broken) == 1
    assert broken[0].severity == Severity.HIGH
    assert "missing.gd" in broken[0].description


def test_unused_declared_resource(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    # Script exists
    script = tmp_path / "player.gd"
    script.write_text("extends Node\n", encoding="utf-8")

    # Scene declares ext_resource id="1_abc" but never references it in any node
    scene = tmp_path / "test.tscn"
    scene.write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://player.gd" id="1_abc"]
[node name="Node" type="Node"]
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = ResourceAnalyzer(project)
    issues = analyzer.audit_scene_resources()

    unused = [i for i in issues if i.category == IssueCategory.UNUSED_RESOURCE]
    assert len(unused) == 1
    assert unused[0].severity == Severity.MEDIUM
    assert "1_abc" in (unused[0].context or "") or "1_abc" in unused[0].description


def test_orphan_asset_detection(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """config_version=5
[application]
run/main_scene="res://main.tscn"
""",
        encoding="utf-8",
    )

    # Main scene exists and is referenced in project.godot
    (tmp_path / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    # Orphan texture exists on disk but is not in any scene or script
    (tmp_path / "unused_art.png").write_bytes(b"dummy image bytes")

    project = GodotProject(tmp_path)
    analyzer = ResourceAnalyzer(project)
    issues = analyzer.audit_orphan_assets()

    orphans = [i for i in issues if i.category == IssueCategory.ORPHAN_ASSET]
    assert len(orphans) == 1
    assert "unused_art.png" in orphans[0].title
