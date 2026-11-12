"""Indirect hot loop tracking only follows calls that run on every frame, unguarded."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.gdscript import GDScriptAnalyzer
from gdaudit.models import AuditIssue, IssueCategory
from gdaudit.utils.godot_project import GodotProject

HELPER = """
func helper() -> void:
\tvar n = get_node("x")
"""


def _indirect(tmp_path: Path, process_body: str, tail: str = HELPER) -> list[AuditIssue]:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    source = "extends Node\n\nfunc _process(delta: float) -> void:\n" + process_body + tail
    (tmp_path / "s.gd").write_text(source, encoding="utf-8")
    issues = GDScriptAnalyzer(GodotProject(tmp_path)).analyze()
    return [
        i
        for i in issues
        if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN and "(indirect)" in i.title
    ]


def test_base_level_call_reports(tmp_path: Path) -> None:
    found = _indirect(tmp_path, "\thelper()\n")
    assert len(found) == 1
    assert "_process -> helper" in found[0].title
    assert "at line 4" in found[0].description


def test_call_inside_if_does_not_report(tmp_path: Path) -> None:
    assert _indirect(tmp_path, "\tif delta > 1.0:\n\t\thelper()\n") == []


def test_call_inside_elif_does_not_report(tmp_path: Path) -> None:
    body = "\tif delta > 1.0:\n\t\tpass\n\telif delta > 0.5:\n\t\thelper()\n"
    assert _indirect(tmp_path, body) == []


def test_call_inside_else_does_not_report(tmp_path: Path) -> None:
    body = "\tif delta > 1.0:\n\t\tpass\n\telse:\n\t\thelper()\n"
    assert _indirect(tmp_path, body) == []


def test_call_inside_match_does_not_report(tmp_path: Path) -> None:
    body = "\tmatch state:\n\t\t1:\n\t\t\thelper()\n\t\t2:\n\t\t\tpass\n"
    assert _indirect(tmp_path, body) == []


def test_call_on_match_branch_line_does_not_report(tmp_path: Path) -> None:
    body = "\tmatch state:\n\t\t1: helper()\n"
    assert _indirect(tmp_path, body) == []


def test_one_line_guard_does_not_report(tmp_path: Path) -> None:
    assert _indirect(tmp_path, "\tif delta > 1.0: helper()\n") == []


def test_call_inside_for_reports(tmp_path: Path) -> None:
    found = _indirect(tmp_path, "\tfor i in range(3):\n\t\thelper()\n")
    assert len(found) == 1
    assert "at line 5" in found[0].description


def test_call_inside_while_reports(tmp_path: Path) -> None:
    found = _indirect(tmp_path, "\twhile delta > 0.0:\n\t\thelper()\n\t\tbreak\n")
    assert len(found) == 1
    assert "at line 5" in found[0].description


def test_call_inside_for_inside_if_does_not_report(tmp_path: Path) -> None:
    body = "\tif delta > 1.0:\n\t\tfor i in range(3):\n\t\t\thelper()\n"
    assert _indirect(tmp_path, body) == []


def test_later_unconditional_call_is_the_one_cited(tmp_path: Path) -> None:
    body = "\tif delta > 1.0:\n\t\thelper()\n\thelper()\n"
    found = _indirect(tmp_path, body)
    assert len(found) == 1
    assert "at line 6" in found[0].description


def test_call_in_if_condition_reports(tmp_path: Path) -> None:
    found = _indirect(tmp_path, "\tif helper() == null:\n\t\tpass\n")
    assert len(found) == 1
    assert "at line 4" in found[0].description


def test_real_world_guarded_calls_do_not_report(tmp_path: Path) -> None:
    tail = """
func _on_restart_pressed() -> void:
\tget_tree().reload_current_scene()
\tvar n = get_node("x")

func spawn_horde() -> void:
\tvar e = Enemy.new()
"""
    body = """\tif is_game_over:
\t\tif Input.is_key_pressed(KEY_R):
\t\t\t_on_restart_pressed()
\t\treturn
\tspawn_timer += delta
\tif spawn_timer >= spawn_interval:
\t\tspawn_timer = 0.0
\t\tspawn_horde()
"""
    assert _indirect(tmp_path, body, tail) == []


def test_real_world_unconditional_update_ui_reports(tmp_path: Path) -> None:
    tail = """
func update_ui() -> void:
\tget_node("x").text = "hi"
"""
    found = _indirect(tmp_path, "\tupdate_ui()\n", tail)
    assert len(found) == 1
    assert "_process -> update_ui" in found[0].title
