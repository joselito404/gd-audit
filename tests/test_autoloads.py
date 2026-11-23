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
    assert cycle_issues[0].file_path == "manager_a.gd"
    assert "ManagerA" in cycle_issues[0].description
    assert "ManagerB" in cycle_issues[0].description


def test_autoload_cycle_uses_relative_posix_file_path(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """
config_version=5
[autoload]
Alpha="*res://autoloads/alpha.gd"
Beta="*res://autoloads/beta.gd"
""",
        encoding="utf-8",
    )
    sub = tmp_path / "autoloads"
    sub.mkdir()
    (sub / "alpha.gd").write_text("extends Node\nfunc f(): Beta.g()\n", encoding="utf-8")
    (sub / "beta.gd").write_text("extends Node\nfunc g(): Alpha.f()\n", encoding="utf-8")

    issues = AutoloadAnalyzer(GodotProject(tmp_path)).analyze()
    cycles = [i for i in issues if i.category == IssueCategory.AUTOLOAD_CYCLE]
    assert len(cycles) == 1
    assert cycles[0].file_path == "autoloads/alpha.gd"
    assert not cycles[0].file_path.startswith("res://")


def test_autoload_ignores_references_inside_strings_and_multiline_strings(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """
config_version=5
[autoload]
Alpha="*res://alpha.gd"
Beta="*res://beta.gd"
""",
        encoding="utf-8",
    )
    (tmp_path / "alpha.gd").write_text(
        '''extends Node
var doc = """
Beta should not trigger a cycle here
"""
var doc2 = \'\'\'
Beta in triple single quotes
\'\'\'
func _ready():
    print("Beta is mentioned in a log string")
    # Beta in a comment
''',
        encoding="utf-8",
    )
    (tmp_path / "beta.gd").write_text(
        "extends Node\nfunc _ready():\n    Alpha.ping()\n",
        encoding="utf-8",
    )

    analyzer = AutoloadAnalyzer(GodotProject(tmp_path))
    graph = analyzer.build_graph()
    assert graph["Alpha"] == set()
    assert graph["Beta"] == {"Alpha"}
    assert analyzer.detect_cycles() == []


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


def test_autoload_cycle_fallback_when_res_to_path_raises(tmp_path: Path, monkeypatch) -> None:
    import gdaudit.core.autoloads as auto_mod

    (tmp_path / "project.godot").write_text(
        'config_version=5\n[autoload]\nA="*res://a.gd"\nB="*res://b.gd"\n',
        encoding="utf-8",
    )
    (tmp_path / "a.gd").write_text("extends Node\nfunc f(): B.g()\n", encoding="utf-8")
    (tmp_path / "b.gd").write_text("extends Node\nfunc g(): A.f()\n", encoding="utf-8")

    project = GodotProject(tmp_path)
    analyzer = AutoloadAnalyzer(project)
    analyzer.build_graph()

    def raise_value_error(*_args, **_kwargs):
        raise ValueError("bad path")

    monkeypatch.setattr(auto_mod, "res_to_path", raise_value_error)
    cycles = analyzer.detect_cycles()
    assert len(cycles) == 1
    assert cycles[0].file_path == "a.gd"
