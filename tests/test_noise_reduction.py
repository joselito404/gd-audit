"""Tests for false-positive reduction: GUT scripts, addons, .gdignore and dynamic res:// paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gdaudit.cli import app
from gdaudit.core.gdscript import GDScriptAnalyzer, fast_clean_gdscript_comments
from gdaudit.core.resources import ResourceAnalyzer
from gdaudit.models import IssueCategory
from gdaudit.utils.godot_project import GodotProject, dynamic_res_prefix, is_test_script

GUT_SCRIPT = """extends GutTest

func before_each():
    pass

func after_each():
    pass

func before_all():
    pass

func after_all():
    pass

func test_algo():
    assert_true(true)

func helper_never_called():
    pass
"""


def _make_project(root: Path, main_scene: str | None = None) -> None:
    text = "config_version=5\n"
    if main_scene:
        text += f'[application]\nrun/main_scene="{main_scene}"\n'
    (root / "project.godot").write_text(text, encoding="utf-8")


def _dead_names(project: GodotProject) -> list[str]:
    issues = GDScriptAnalyzer(project).audit_dead_functions()
    return [i.title for i in issues if i.category == IssueCategory.DEAD_FUNCTION]


def _orphans(project: GodotProject) -> list[str]:
    issues = ResourceAnalyzer(project).audit_orphan_assets()
    return [i.file_path or "" for i in issues if i.category == IssueCategory.ORPHAN_ASSET]


# --- 1. GUT / GdUnit test methods are not dead functions -------------------------------------


def test_gut_test_methods_and_hooks_not_dead(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "test_thing.gd").write_text(GUT_SCRIPT, encoding="utf-8")

    names = _dead_names(GodotProject(tmp_path))

    assert not any("test_algo" in n for n in names)
    for hook in ("before_each", "after_each", "before_all", "after_all"):
        assert not any(f"'{hook}'" in n for n in names)
    # Ordinary helpers inside a test script can still be dead
    assert any("helper_never_called" in n for n in names)


def test_test_prefix_in_normal_script_still_dead(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "normal.gd").write_text(
        "extends Node\n\nfunc test_algo():\n    pass\n\nfunc before_each():\n    pass\n",
        encoding="utf-8",
    )

    names = _dead_names(GodotProject(tmp_path))

    assert any("test_algo" in n for n in names)
    assert any("before_each" in n for n in names)


@pytest.mark.parametrize(
    "header",
    [
        "extends GutTest",
        "class_name MyTest extends GutTest",
        "extends GdUnitTestSuite",
        'extends "res://addons/gut/test.gd"',
    ],
)
def test_is_test_script_detects_headers(header: str) -> None:
    assert is_test_script(f"{header}\n\nfunc test_x():\n    pass\n")


def test_is_test_script_rejects_other_scripts() -> None:
    assert not is_test_script("extends Node\n")
    assert not is_test_script("extends GutTestHelper\n")
    # Commented-out extends are masked by the comment cleaner before detection
    assert not is_test_script(fast_clean_gdscript_comments("# extends GutTest\nextends Node\n"))


def test_gdunit_suite_methods_not_dead(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "suite.gd").write_text(
        "extends GdUnitTestSuite\n\nfunc test_it():\n    pass\n\nfunc before():\n    pass\n",
        encoding="utf-8",
    )

    names = _dead_names(GodotProject(tmp_path))

    assert not any("test_it" in n for n in names)


# --- 2. addons/ ignored by default, only at the project root ---------------------------------


def _addons_project(root: Path) -> None:
    _make_project(root)
    addon = root / "addons" / "plugin"
    addon.mkdir(parents=True)
    (addon / "plugin.gd").write_text("extends Node\nfunc addon_unused():\n    pass\n", "utf-8")
    (addon / "icon.png").write_bytes(b"png")
    # A nested folder with the same name is NOT the root addons folder
    nested = root / "scripts" / "addons"
    nested.mkdir(parents=True)
    (nested / "tool.gd").write_text("extends Node\nfunc nested_unused():\n    pass\n", "utf-8")


def test_addons_ignored_by_default(tmp_path: Path) -> None:
    _addons_project(tmp_path)
    project = GodotProject(tmp_path)

    names = _dead_names(project)
    orphans = _orphans(project)

    assert not any("addon_unused" in n for n in names)
    assert not any(o.startswith("addons/") for o in orphans)
    # Nested 'addons' folder is analyzed normally
    assert any("nested_unused" in n for n in names)
    assert "scripts/addons/tool.gd" in orphans


def test_include_addons_analyzes_root_addons(tmp_path: Path) -> None:
    _addons_project(tmp_path)
    project = GodotProject(tmp_path, include_addons=True)

    names = _dead_names(project)
    orphans = _orphans(project)

    assert any("addon_unused" in n for n in names)
    assert "addons/plugin/icon.png" in orphans


def test_ext_resource_into_ignored_addons_is_not_broken(tmp_path: Path) -> None:
    _make_project(tmp_path, "res://main.tscn")
    addon = tmp_path / "addons" / "plugin"
    addon.mkdir(parents=True)
    (addon / "helper.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "main.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://addons/plugin/helper.gd" id="1_a"]
[node name="Node" type="Node"]
script = ExtResource("1_a")
""",
        encoding="utf-8",
    )

    issues = ResourceAnalyzer(GodotProject(tmp_path)).audit_scene_resources()

    assert not [i for i in issues if i.category == IssueCategory.BROKEN_RESOURCE]


def test_cli_include_addons_flag(tmp_path: Path) -> None:
    _addons_project(tmp_path)
    runner = CliRunner()

    default = runner.invoke(app, ["scan", str(tmp_path), "--format", "json", "--fail-on", "never"])
    included = runner.invoke(
        app, ["scan", str(tmp_path), "--format", "json", "--fail-on", "never", "--include-addons"]
    )

    default_files = {i["file_path"] for i in json.loads(default.output)["issues"]}
    included_files = {i["file_path"] for i in json.loads(included.output)["issues"]}
    assert not any(f.startswith("addons/") for f in default_files)
    assert any(f.startswith("addons/") for f in included_files)


# --- 3. .gdignore ----------------------------------------------------------------------------


def test_gdignore_folder_is_excluded_with_contents(tmp_path: Path) -> None:
    _make_project(tmp_path)
    shots = tmp_path / "capturas"
    (shots / "deep" / "er").mkdir(parents=True)
    (shots / ".gdignore").write_text("", encoding="utf-8")
    (shots / "shot.png").write_bytes(b"png")
    (shots / "deep" / "er" / "other.png").write_bytes(b"png")
    (shots / "deep" / "script.gd").write_text("extends Node\nfunc ghost():\n    pass\n", "utf-8")
    (tmp_path / "kept.png").write_bytes(b"png")

    project = GodotProject(tmp_path)

    assert _orphans(project) == ["kept.png"]
    assert not any("capturas" in str(f) for f in project.all_files)
    assert not any("ghost" in n for n in _dead_names(project))
    assert project.is_ignored(shots / "deep" / "er" / "other.png")
    assert project.is_ignored(shots / "deep")
    assert not project.is_ignored(tmp_path / "kept.png")


# --- 4. test scripts are not orphan assets ---------------------------------------------------


def test_test_scripts_not_orphans_but_other_scripts_are(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "test_thing.gd").write_text(GUT_SCRIPT, encoding="utf-8")
    (tmp_path / "suite.gd").write_text("extends GdUnitTestSuite\n", encoding="utf-8")
    (tmp_path / "lonely.gd").write_text("extends Node\n", encoding="utf-8")

    orphans = _orphans(GodotProject(tmp_path))

    assert "test_thing.gd" not in orphans
    assert "suite.gd" not in orphans
    assert "lonely.gd" in orphans


# --- 5. dynamically built paths --------------------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("res://a/b/c_%s.png", "res://a/b/c_"),
        ("res://assets/ruleta/%s/", "res://assets/ruleta/"),
        ("res://assets/ruleta/3d/casilla_tienda%s.glb", "res://assets/ruleta/3d/casilla_tienda"),
        ("res://assets/bolas/", "res://assets/bolas/"),
        ("res://a/b/", "res://a/b/"),
        ("res://a/b%d", "res://a/b"),
        # Too short: would silence (almost) the whole project
        ("res://a/", None),
        ("res://assets/", None),
        ("res://", None),
        ("res://%s", None),
        ("res://a/%s", None),
        ("res://icon_%s.png", None),
        # Not dynamic
        ("res://a/b/c.png", None),
    ],
)
def test_dynamic_res_prefix(literal: str, expected: str | None) -> None:
    assert dynamic_res_prefix(literal) == expected


def test_percent_placeholder_literal_references_matching_files(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c_x.png").write_bytes(b"png")
    (tmp_path / "a" / "b" / "d_x.png").write_bytes(b"png")
    (tmp_path / "loader.gd").write_text(
        'extends Node\nfunc load_it(t):\n    return load("res://a/b/c_%s.png" % t)\n',
        encoding="utf-8",
    )

    orphans = _orphans(GodotProject(tmp_path))

    assert "a/b/c_x.png" not in orphans
    assert "a/b/d_x.png" in orphans


def test_folder_literal_references_files_below(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "assets" / "bolas").mkdir(parents=True)
    (tmp_path / "assets" / "bolas" / "roja.png").write_bytes(b"png")
    (tmp_path / "assets" / "otros").mkdir(parents=True)
    (tmp_path / "assets" / "otros" / "azul.png").write_bytes(b"png")
    (tmp_path / "loader.gd").write_text(
        'extends Node\nconst CARPETA := "res://assets/bolas/"\n', encoding="utf-8"
    )

    orphans = _orphans(GodotProject(tmp_path))

    assert "assets/bolas/roja.png" not in orphans
    assert "assets/otros/azul.png" in orphans


def test_short_folder_literal_references_nothing(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "img.png").write_bytes(b"png")
    (tmp_path / "loader.gd").write_text('extends Node\nconst D := "res://a/"\n', encoding="utf-8")

    assert "a/img.png" in _orphans(GodotProject(tmp_path))


def test_plain_literal_reference_still_works(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "used.png").write_bytes(b"png")
    (tmp_path / "unused.png").write_bytes(b"png")
    (tmp_path / "loader.gd").write_text(
        'extends Node\nvar t = preload("res://used.png")\n', encoding="utf-8"
    )

    orphans = _orphans(GodotProject(tmp_path))

    assert "used.png" not in orphans
    assert "unused.png" in orphans
