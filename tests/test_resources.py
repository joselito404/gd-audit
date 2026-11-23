"""Unit tests for scene and resource validation (broken, unused, and orphan assets)."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.resources import ResourceAnalyzer, _format_size
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


def test_format_size_floating_point_precision() -> None:
    assert _format_size(512) == "512 B"
    assert _format_size(1536) == "1.5 KB"
    assert _format_size(int(2.5 * 1024 * 1024)) == "2.5 MB"
    assert _format_size(int(1.5 * 1024 * 1024 * 1024)) == "1.5 GB"


def test_orphan_assets_normalizes_main_scene_and_autoload_paths(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """config_version=5
[application]
run/main_scene="res://./main.tscn"
[autoload]
Global="*res://scripts/../global.gd"
""",
        encoding="utf-8",
    )
    (tmp_path / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (tmp_path / "global.gd").write_text("extends Node\n", encoding="utf-8")

    project = GodotProject(tmp_path)
    orphans = ResourceAnalyzer(project).audit_orphan_assets()
    assert orphans == []


def test_format_size_terabytes() -> None:
    assert _format_size(2 * (1024**4)) == "2.0 TB"


def test_scene_cycle_detection_and_export_packed_scene_excluded(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    # Direct cycle: a.tscn -> b.tscn -> a.tscn
    (tmp_path / "a.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="PackedScene" path="res://b.tscn" id="1"]\n'
        '[node name="A" type="Node"]\n'
        '[node name="BInst" parent="." instance=ExtResource("1")]\n',
        encoding="utf-8",
    )
    (tmp_path / "b.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="PackedScene" path="res://a.tscn" id="1"]\n'
        '[node name="B" type="Node"]\n'
        '[node name="AInst" parent="." instance=ExtResource("1")]\n',
        encoding="utf-8",
    )

    # Non-cycle: c.tscn references d.tscn only as an @export PackedScene property, not instance=
    (tmp_path / "c.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="PackedScene" path="res://d.tscn" id="1"]\n'
        '[node name="C" type="Node"]\n'
        'next_scene = ExtResource("1")\n',
        encoding="utf-8",
    )
    (tmp_path / "d.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="PackedScene" path="res://c.tscn" id="1"]\n'
        '[node name="D" type="Node"]\n'
        'prev_scene = ExtResource("1")\n',
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    cycles = ResourceAnalyzer(project).audit_scene_cycles()
    assert len(cycles) == 1
    assert cycles[0].category == IssueCategory.SCENE_CYCLE
    assert cycles[0].severity == Severity.HIGH
    assert "a.tscn" in cycles[0].description and "b.tscn" in cycles[0].description


def test_scene_self_cycle_detection(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "loop.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="PackedScene" path="res://loop.tscn" id="1"]\n'
        '[node name="Loop" type="Node"]\n'
        '[node name="Self" parent="." instance=ExtResource("1")]\n',
        encoding="utf-8",
    )
    cycles = ResourceAnalyzer(GodotProject(tmp_path)).audit_scene_cycles()
    assert len(cycles) == 1
    assert cycles[0].category == IssueCategory.SCENE_CYCLE


def test_broken_resource_path_in_gdscript_and_project_godot(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        "config_version=5\n"
        "[application]\n"
        'run/main_scene="res://missing_main.tscn"\n'
        'config/icon="res://missing_icon.svg"\n'
        "[autoload]\n"
        'BrokenAuto="*res://missing_auto.gd"\n',
        encoding="utf-8",
    )
    (tmp_path / "existing.tres").write_text("[gd_resource format=3]\n", encoding="utf-8")
    (tmp_path / "loader.gd").write_text(
        "extends Node\n"
        'const OK = preload("res://existing.tres")\n'
        'const BAD1 = preload("res://nope1.tscn")\n'
        "func _ready():\n"
        '    var a = load("res://nope2.gd")\n'
        '    var b = ResourceLoader.load("res://nope3.tres")\n'
        '    var f = FileAccess.open("res://nope4.dat", FileAccess.READ)\n'
        '    var w = FileAccess.open("res://new_save.dat", FileAccess.WRITE)\n'
        '    var dyn1 = load("res://levels/" + name)\n'
        '    var dyn2 = load("res://levels/%s.tscn" % name)\n'
        '    var dir_prefix = load("res://levels/")\n'
        '    # var commented = load("res://commented_out.gd")\n',
        encoding="utf-8",
    )
    # Test scripts must be ignored
    (tmp_path / "test_suite.gd").write_text(
        'extends GutTest\nfunc test_bad():\n    load("res://intentionally_missing.gd")\n',
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = ResourceAnalyzer(project).audit_broken_resource_paths()
    assert all(i.category == IssueCategory.BROKEN_RESOURCE_PATH for i in issues)
    assert all(i.severity == Severity.HIGH for i in issues)

    titles = {i.title for i in issues}
    assert "Broken Resource Path: res://nope1.tscn" in titles
    assert "Broken Resource Path: res://nope2.gd" in titles
    assert "Broken Resource Path: res://nope3.tres" in titles
    assert "Broken Resource Path: res://nope4.dat" in titles
    assert "Broken Project Resource Path: res://missing_main.tscn" in titles
    assert "Broken Project Resource Path: res://missing_icon.svg" in titles
    assert "Broken Project Resource Path: res://missing_auto.gd" in titles
    assert len(issues) == 7


def test_broken_signal_connection_detection(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "base.gd").write_text(
        "class_name BaseUI\nextends Control\nfunc _on_inherited_signal():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "ui.gd").write_text(
        "extends BaseUI\nfunc _on_valid_pressed():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "child.gd").write_text(
        'extends "res://base.gd"\nfunc _on_child_ok():\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "ui.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="Script" path="res://ui.gd" id="1"]\n'
        '[ext_resource type="Script" path="res://child.gd" id="2"]\n'
        '[node name="UI" type="Control"]\n'
        'script = ExtResource("1")\n'
        '[node name="Panel" type="Panel" parent="."]\n'
        'script = ExtResource("2")\n'
        '[node name="Btn" type="Button" parent="Panel"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="_on_valid_pressed"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="_on_inherited_signal"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="queue_free"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="_on_missing_handler"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="Panel" method="_on_child_ok"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="Panel" method="_on_child_missing"]\n'
        '[connection signal="pressed" from="Panel/Btn" to="Panel/Btn" method="hide"]\n',
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = ResourceAnalyzer(project).audit_broken_signal_connections()
    assert len(issues) == 2
    assert all(i.category == IssueCategory.BROKEN_SIGNAL_CONNECTION for i in issues)
    assert all(i.severity == Severity.HIGH for i in issues)
    titles = {i.title for i in issues}
    assert "Broken Signal Connection: _on_missing_handler" in titles
    assert "Broken Signal Connection: _on_child_missing" in titles


def test_uid_ext_resource_resolution_and_unresolved_uid(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "hero.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "hero.gd.uid").write_text("uid://hero_uid_1\n", encoding="utf-8")
    (tmp_path / "level.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="Script" uid="uid://hero_uid_1" id="1"]\n'
        '[ext_resource type="Texture2D" uid="uid://external_unindexed" id="2"]\n'
        '[node name="Level" type="Node"]\n'
        'script = ExtResource("1")\n',
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = ResourceAnalyzer(project)
    decls, _ = analyzer.parse_ext_resources(tmp_path / "level.tscn")
    assert len(decls) == 2
    assert decls[0].path == "res://hero.gd"
    assert decls[1].path == "uid://external_unindexed"

    issues = analyzer.audit_scene_resources()
    broken = [i for i in issues if i.category == IssueCategory.BROKEN_RESOURCE]
    unused = [i for i in issues if i.category == IssueCategory.UNUSED_RESOURCE]
    assert broken == []
    assert len(unused) == 1
    assert "uid://external_unindexed" in unused[0].title


def test_resources_io_errors_and_analyze_entrypoint(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "orphan.png").write_bytes(b"png")
    (tmp_path / "export_presets.cfg").write_text("", encoding="utf-8")

    project = GodotProject(tmp_path)
    analyzer = ResourceAnalyzer(project)

    # parse_ext_resources on nonexistent path hits OSError branch cleanly
    decls, body = analyzer.parse_ext_resources(tmp_path / "nonexistent.tscn")
    assert decls == [] and body == ""

    # _class_name_is_referenced when file is not in file_texts
    assert analyzer._class_name_is_referenced(tmp_path / "ghost.gd") is False

    # analyze(check_orphans=False) vs True
    assert analyzer.analyze(check_orphans=False) == []
    with_orphans = analyzer.analyze(check_orphans=True)
    assert len(with_orphans) == 1

    # stat() OSError in audit_orphan_assets is handled gracefully
    orig_stat = Path.stat

    def failing_stat(self: Path, *args, **kwargs):
        if self.name == "orphan.png":
            raise OSError("stat failed")
        return orig_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    orphans_no_size = analyzer.audit_orphan_assets()
    assert len(orphans_no_size) == 1
    assert "orphan.png" in orphans_no_size[0].title
