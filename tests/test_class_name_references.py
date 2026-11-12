"""Tests for class_name references: a script used by its class name is not an orphan asset."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.resources import ResourceAnalyzer
from gdaudit.models import IssueCategory
from gdaudit.utils.godot_project import GodotProject


def _make_project(root: Path) -> None:
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")


def _orphan_files(root: Path) -> set[str]:
    issues = ResourceAnalyzer(GodotProject(root)).audit_orphan_assets()
    return {i.file_path or "" for i in issues if i.category == IssueCategory.ORPHAN_ASSET}


def test_class_name_used_by_name_in_other_script_is_not_orphan(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "foo.gd").write_text("class_name Foo\nextends RefCounted\n", encoding="utf-8")
    (tmp_path / "main.gd").write_text(
        "extends Node\n\nfunc _ready() -> void:\n    var f := Foo.new()\n",
        encoding="utf-8",
    )

    orphans = _orphan_files(tmp_path)

    assert "foo.gd" not in orphans
    # Control: a script that nobody names or loads by path is still an orphan
    assert "main.gd" in orphans


def test_class_name_nobody_uses_stays_orphan(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "bar.gd").write_text("class_name Bar\nextends Node\n", encoding="utf-8")
    (tmp_path / "other.gd").write_text("extends Node\nvar x := 1\n", encoding="utf-8")

    assert "bar.gd" in _orphan_files(tmp_path)


def test_script_without_class_name_or_path_reference_stays_orphan(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "helper.gd").write_text("extends Node\n", encoding="utf-8")

    assert "helper.gd" in _orphan_files(tmp_path)


def test_class_name_only_in_its_own_file_stays_orphan(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "baz.gd").write_text(
        "class_name Baz\nextends Node\n\nvar item: Baz\n\n"
        "func make() -> Baz:\n    return Baz.new()\n",
        encoding="utf-8",
    )

    assert "baz.gd" in _orphan_files(tmp_path)


def test_class_name_matches_whole_word_only(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "foo.gd").write_text("class_name Foo\nextends Node\n", encoding="utf-8")
    (tmp_path / "main.gd").write_text("extends Node\nvar x := FooBar.new()\n", encoding="utf-8")

    assert "foo.gd" in _orphan_files(tmp_path)


def test_commented_class_name_is_not_a_declaration(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "qux.gd").write_text("# class_name Qux\nextends Node\n", encoding="utf-8")
    (tmp_path / "user.gd").write_text("extends Node\nvar q := Qux.new()\n", encoding="utf-8")

    assert "qux.gd" in _orphan_files(tmp_path)


def test_class_name_used_as_node_type_in_scene_is_not_orphan(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "ruleta.gd").write_text("class_name Ruleta3D\nextends Node3D\n", encoding="utf-8")
    (tmp_path / "mesa.tscn").write_text(
        '[gd_scene format=3]\n\n[node name="Mesa" type="Ruleta3D"]\n',
        encoding="utf-8",
    )

    assert "ruleta.gd" not in _orphan_files(tmp_path)
