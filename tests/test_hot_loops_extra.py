"""Comprehensive unit tests for extended hot loop detectors and 1-level indirect call tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdaudit.core.gdscript import GDScriptAnalyzer, mask_gdscript_code
from gdaudit.models import IssueCategory, Severity
from gdaudit.utils.godot_project import GodotProject


def _make_project(
    root: Path, script_content: str, script_name: str = "test_script.gd"
) -> GodotProject:
    """Helper to initialize a minimal GodotProject on disk."""
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (root / script_name).write_text(script_content, encoding="utf-8")
    return GodotProject(root)


# --- 1. Positive Tests for New Hot Loop Patterns in _process / _physics_process ---


def test_regex_new_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var regex = RegEx.new()

func _physics_process(delta: float) -> void:
    var regex2 = RegEx.new()
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 2
    assert all(i.severity == Severity.MEDIUM for i in issues)
    assert any("RegEx.new()" in i.title and "_process()" in i.title for i in issues)
    assert any("RegEx.new()" in i.title and "_physics_process()" in i.title for i in issues)


def test_regex_create_from_string_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var regex = RegEx.create_from_string("^[a-z]+$")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert "RegEx.new()" in issue.title
    assert "RegEx.create_from_string(" in issue.description
    assert issue.line_number == 4


def test_file_access_open_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var file = FileAccess.open("user://savegame.dat", FileAccess.WRITE)
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert "FileAccess.open()" in issue.title
    assert "FileAccess.open(" in issue.description
    assert issue.line_number == 4


def test_dir_access_open_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _physics_process(delta: float) -> void:
    var dir = DirAccess.open("res://levels")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert "DirAccess.open()" in issue.title
    assert "DirAccess.open(" in issue.description
    assert issue.line_number == 4


def test_get_tree_get_nodes_in_group_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var enemies = get_tree().get_nodes_in_group("enemies")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert "get_nodes_in_group()" in issue.title
    assert "get_tree().get_nodes_in_group(" in issue.description
    assert issue.line_number == 4


def test_get_nodes_in_group_standalone_in_hot_loop(tmp_path: Path) -> None:
    source = """extends SceneTree

func _process(delta: float) -> void:
    var items = get_nodes_in_group("items")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert "get_nodes_in_group()" in issue.title
    assert "get_nodes_in_group(" in issue.description
    assert issue.line_number == 4


def test_instantiate_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

var bullet_scene: PackedScene

func _process(delta: float) -> void:
    var b = bullet_scene.instantiate()
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert ".instantiate() / .new()" in issue.title
    assert ".instantiate(" in issue.description
    assert issue.line_number == 6


def test_new_in_hot_loop(tmp_path: Path) -> None:
    source = """extends Node

func _physics_process(delta: float) -> void:
    var instance = RefCounted.new()
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.MEDIUM
    assert ".instantiate() / .new()" in issue.title
    assert ".new(" in issue.description
    assert issue.line_number == 4


# --- 2. Indirect 1-Level Call Tracking ---


def test_indirect_one_level_call_low_severity(tmp_path: Path) -> None:
    source = """extends CharacterBody2D

func _process(delta: float) -> void:
    # _process calls helper_func
    helper_func()

func helper_func() -> void:
    # helper_func has the expensive call
    var target = get_node("Target")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.LOW
    assert "(indirect)" in issue.title
    assert "_process -> helper_func" in issue.title
    assert "_process -> helper_func" in issue.description
    assert issue.line_number == 9
    assert "get_node(" in issue.description
    assert issue.context == 'var target = get_node("Target")'


def test_indirect_call_with_self_syntax(tmp_path: Path) -> None:
    source = """extends Node

func _physics_process(delta: float) -> void:
    self.spawn_particle()

func spawn_particle() -> void:
    var p = particle_scene.instantiate()
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == Severity.LOW
    assert "_physics_process -> spawn_particle" in issue.title
    assert "_physics_process -> spawn_particle" in issue.description
    assert issue.line_number == 7


def test_both_direct_and_indirect_issues_reported(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var direct_node = get_node("Direct")
    update_data()

func update_data() -> void:
    var f = FileAccess.open("user://data.bin", FileAccess.READ)
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert len(issues) == 2

    direct = [i for i in issues if i.severity == Severity.MEDIUM]
    indirect = [i for i in issues if i.severity == Severity.LOW]
    assert len(direct) == 1
    assert len(indirect) == 1

    assert direct[0].line_number == 4
    assert "get_node()" in direct[0].title

    assert indirect[0].line_number == 8
    assert "_process -> update_data" in indirect[0].title


# --- 3. Negative Tests: Comments, Strings, Preload, and Non-Hot-Loop Functions ---


def test_negative_comments_not_flagged(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    # get_node("Commented")
    # find_child("Weapon")
    # ResourceLoader.load("res://icon.png")
    # load("res://icon.png")
    # RegEx.new()
    # RegEx.create_from_string("pattern")
    # FileAccess.open("user://save.dat")
    # DirAccess.open("res://dir")
    # get_tree().get_nodes_in_group("enemies")
    # get_nodes_in_group("enemies")
    # bullet.instantiate()
    # MyClass.new()
    # $Sprite
    pass
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert issues == []


def test_negative_string_literals_not_flagged(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var s1 = "get_node('Fake')"
    var s2 = 'FileAccess.open("path")'
    var s3 = "DirAccess.open('path')"
    var s4 = "RegEx.new()"
    var s5 = 'RegEx.create_from_string("pat")'
    var s6 = "get_tree().get_nodes_in_group('grp')"
    var s7 = 'get_nodes_in_group("grp")'
    var s8 = "scene.instantiate()"
    var s9 = 'MyClass.new()'
    var s10 = \"\"\"
    FileAccess.open("multiline")
    RegEx.new()
    \"\"\"
    var s11 = '''
    DirAccess.open("multiline_single")
    '''
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert issues == []


def test_negative_preload_allowed(tmp_path: Path) -> None:
    source = """extends Node

func _process(delta: float) -> void:
    var tex = preload("res://icon.svg")
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert issues == []


def test_negative_outside_hot_loops(tmp_path: Path) -> None:
    source = """extends Node

func _ready() -> void:
    var r = RegEx.new()
    var r2 = RegEx.create_from_string("pattern")
    var f = FileAccess.open("user://save.dat", FileAccess.READ)
    var d = DirAccess.open("res://assets")
    var g = get_tree().get_nodes_in_group("all")
    var g2 = get_nodes_in_group("all")
    var inst = my_scene.instantiate()
    var obj = RefCounted.new()
    var n = get_node("Sprite")

func _enter_tree() -> void:
    var n2 = get_node("Sprite")

func uncalled_helper() -> void:
    var f = FileAccess.open("user://log.txt", FileAccess.WRITE)
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert issues == []


def test_negative_indirect_not_called_by_process(tmp_path: Path) -> None:
    source = """extends Node

func _ready() -> void:
    # Called by _ready, NOT by _process
    ready_helper()

func ready_helper() -> void:
    var f = FileAccess.open("user://save.dat", FileAccess.READ)

func _process(delta: float) -> void:
    # Calling method on an external object, NOT local function
    other_object.ready_helper()
    # String mentioning function name
    var s = "ready_helper()"
"""
    project = _make_project(tmp_path, source)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN
    ]
    assert issues == []


# --- 4. Unit Test for mask_gdscript_code Utility ---


@pytest.mark.parametrize(
    ("input_str", "should_contain", "should_not_contain"),
    [
        ('# get_node("test")', "", 'get_node("test")'),
        ('var x = "FileAccess.open()"', "var x = ", "FileAccess.open()"),
        ('var x = $"Sprite/Arm"', '$"__________"', "Sprite/Arm"),
    ],
)
def test_mask_gdscript_code(input_str: str, should_contain: str, should_not_contain: str) -> None:
    masked = mask_gdscript_code(input_str)
    assert len(masked) == len(input_str)
    if should_contain:
        assert should_contain in masked
    if should_not_contain:
        assert should_not_contain not in masked
