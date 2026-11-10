"""Unit tests for Autoload circular dependency and unused singleton detectors."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.autoloads import AutoloadAnalyzer
from gdaudit.models import IssueCategory, Severity
from gdaudit.utils.godot_project import GodotProject


def test_detect_circular_autoloads(tmp_path: Path) -> None:
    # Set up project.godot with two autoloads mutually referencing each other
    (tmp_path / "project.godot").write_text(
        """
config_version=5
[autoload]
ManagerA="*res://manager_a.gd"
ManagerB="*res://manager_b.gd"
""",
        encoding="utf-8",
    )

    # ManagerA calls ManagerB
    (tmp_path / "manager_a.gd").write_text(
        """
extends Node
func _ready():
    ManagerB.do_something()
""",
        encoding="utf-8",
    )

    # ManagerB calls ManagerA -> cycle!
    (tmp_path / "manager_b.gd").write_text(
        """
extends Node
func do_something():
    ManagerA.callback()
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = AutoloadAnalyzer(project)
    issues = analyzer.analyze()

    cycle_issues = [i for i in issues if i.category == IssueCategory.AUTOLOAD_CYCLE]
    assert len(cycle_issues) == 1
    assert cycle_issues[0].severity == Severity.HIGH
    assert "ManagerA" in cycle_issues[0].description
    assert "ManagerB" in cycle_issues[0].description


def test_detect_unused_autoload(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """
config_version=5
[autoload]
ActiveManager="*res://active.gd"
UnusedManager="*res://unused.gd"
""",
        encoding="utf-8",
    )

    (tmp_path / "active.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "unused.gd").write_text("extends Node\n", encoding="utf-8")

    # Main scene calls ActiveManager, but nobody calls UnusedManager
    (tmp_path / "main.gd").write_text(
        """
extends Node
func _ready():
    ActiveManager.ping()
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = AutoloadAnalyzer(project)
    issues = analyzer.analyze()

    unused_issues = [i for i in issues if i.category == IssueCategory.UNUSED_AUTOLOAD]
    assert len(unused_issues) == 1
    assert "UnusedManager" in unused_issues[0].title
