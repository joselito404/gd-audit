"""Unit tests for GDScript comment cleaning, hot loops, and dead function detection."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.gdscript import (
    GDScriptAnalyzer,
    clean_gdscript_comments,
)
from gdaudit.models import IssueCategory, Severity
from gdaudit.utils.godot_project import GodotProject


def test_clean_gdscript_comments() -> None:
    source = """# Header comment
var greeting = "Hello # World" # Inline comment
var multiline = \"\"\"
# inside triple double
\"\"\"
var single_comment = 'test # string' # trailing
"""
    cleaned = clean_gdscript_comments(source)
    assert "Header comment" not in cleaned
    assert '"Hello # World"' in cleaned
    assert "'test # string'" in cleaned
    assert "# inside triple double" in cleaned
    assert "trailing" not in cleaned


def test_hot_loop_anti_patterns(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    script = tmp_path / "actor.gd"
    script.write_text(
        """extends CharacterBody2D

func _ready():
    # Outside hot loop: fine
    var node = get_node("Sprite")

func _process(delta):
    # Bad: get_node in _process
    var target = get_node("Target")
    var child = find_child("Weapon")
    var tex = load("res://texture.png")
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = GDScriptAnalyzer(project)
    issues = analyzer.analyze()

    hot_loop_issues = [i for i in issues if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN]
    assert len(hot_loop_issues) >= 3
    assert all(i.severity == Severity.MEDIUM for i in hot_loop_issues)


def test_dead_function_candidate(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    script = tmp_path / "utils.gd"
    script.write_text(
        """extends Node

func _ready():
    # Built-in virtual method: should NOT be marked dead
    pass

func active_helper():
    pass

func completely_unused_func():
    pass
""",
        encoding="utf-8",
    )

    # active_helper is called in another file
    caller = tmp_path / "caller.gd"
    caller.write_text(
        """extends Node
func do_work():
    Utils.active_helper()
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = GDScriptAnalyzer(project)
    issues = analyzer.audit_dead_functions()

    dead_funcs = [i for i in issues if i.category == IssueCategory.DEAD_FUNCTION]
    names = [i.title for i in dead_funcs]

    assert any("completely_unused_func" in n for n in names)
    assert not any("active_helper" in n for n in names)
    assert not any("_ready" in n for n in names)
