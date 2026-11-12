"""Comprehensive tests for gdaudit.core.autoloads and gdaudit.models.

Achieves >= 90% test coverage and ensures robustness against malformed projects,
corrupted configurations, circular dependencies, and edge cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gdaudit.core.autoloads import (
    AutoloadAnalyzer,
    _extract_script_paths_from_tscn,
    _find_canonical_cycle,
    _get_autoload_source_content,
    _strip_comments,
)
from gdaudit.models import (
    AuditIssue,
    AuditResult,
    IssueCategory,
    Severity,
    is_valid_fail_on,
)
from gdaudit.utils.godot_project import AutoloadEntry, GodotProject

# ==============================================================================
# PART 1: gdaudit.models Tests
# ==============================================================================


class TestSeverityModel:
    """Tests for Severity enum, rankings, comparisons, and parsing."""

    def test_severity_ranks(self) -> None:
        assert Severity.INFO.rank == 0
        assert Severity.LOW.rank == 1
        assert Severity.MEDIUM.rank == 2
        assert Severity.HIGH.rank == 3
        assert Severity.CRITICAL.rank == 4

    @pytest.mark.parametrize(
        ("raw_input", "expected"),
        [
            ("info", Severity.INFO),
            ("information", Severity.INFO),
            ("INFO", Severity.INFO),
            ("  information  ", Severity.INFO),
            ("low", Severity.LOW),
            ("LOW", Severity.LOW),
            ("warn", Severity.MEDIUM),
            ("warning", Severity.MEDIUM),
            ("medium", Severity.MEDIUM),
            ("  WARN  ", Severity.MEDIUM),
            ("high", Severity.HIGH),
            ("error", Severity.HIGH),
            ("ERROR", Severity.HIGH),
            ("crit", Severity.CRITICAL),
            ("critical", Severity.CRITICAL),
            ("CRITICAL", Severity.CRITICAL),
            ("  crit\n", Severity.CRITICAL),
        ],
    )
    def test_severity_from_string_valid(self, raw_input: str, expected: Severity) -> None:
        assert Severity.from_string(raw_input) == expected

    @pytest.mark.parametrize("invalid", ["", "   ", "fatal", "unknown", "never", "off", "123"])
    def test_severity_from_string_invalid(self, invalid: str) -> None:
        with pytest.raises(ValueError, match="Invalid severity level"):
            Severity.from_string(invalid)

    def test_severity_ge_operator(self) -> None:
        # With Severity
        assert Severity.HIGH >= Severity.MEDIUM
        assert Severity.HIGH >= Severity.HIGH
        assert not (Severity.LOW >= Severity.HIGH)

        # With str
        assert Severity.HIGH >= "medium"
        assert Severity.HIGH >= "HIGH"
        assert Severity.MEDIUM >= "warn"
        assert not (Severity.LOW >= "high")

        # With invalid object -> TypeError
        with pytest.raises(TypeError):
            _ = Severity.HIGH >= 42
        with pytest.raises(TypeError):
            _ = Severity.HIGH >= None

    def test_severity_gt_operator(self) -> None:
        # With Severity
        assert Severity.HIGH > Severity.MEDIUM
        assert not (Severity.HIGH > Severity.HIGH)
        assert not (Severity.LOW > Severity.CRITICAL)

        # With str
        assert Severity.HIGH > "medium"
        assert Severity.CRITICAL > "high"
        assert not (Severity.MEDIUM > "high")
        assert not (Severity.MEDIUM > "medium")

        # With invalid object -> TypeError
        with pytest.raises(TypeError):
            _ = Severity.HIGH > 10
        with pytest.raises(TypeError):
            _ = Severity.HIGH > []

    def test_severity_le_operator(self) -> None:
        # With Severity
        assert Severity.LOW <= Severity.MEDIUM
        assert Severity.MEDIUM <= Severity.MEDIUM
        assert not (Severity.HIGH <= Severity.LOW)

        # With str
        assert Severity.LOW <= "medium"
        assert Severity.MEDIUM <= "warn"
        assert not (Severity.CRITICAL <= "low")

        # With invalid object -> TypeError
        with pytest.raises(TypeError):
            _ = Severity.LOW <= 99
        with pytest.raises(TypeError):
            _ = Severity.LOW <= {}

    def test_severity_lt_operator(self) -> None:
        # With Severity
        assert Severity.LOW < Severity.HIGH
        assert not (Severity.HIGH < Severity.HIGH)
        assert not (Severity.CRITICAL < Severity.INFO)

        # With str
        assert Severity.LOW < "high"
        assert Severity.INFO < "critical"
        assert not (Severity.HIGH < "low")
        assert not (Severity.MEDIUM < "medium")

        # With invalid object -> TypeError
        with pytest.raises(TypeError):
            _ = Severity.LOW < 0
        with pytest.raises(TypeError):
            _ = Severity.LOW < set()


class TestFailOnValidation:
    """Tests for is_valid_fail_on function."""

    @pytest.mark.parametrize(
        "val",
        [
            "never",
            "NEVER",
            "  never  ",
            "none",
            "NONE",
            "off",
            "OFF",
            "info",
            "low",
            "medium",
            "high",
            "critical",
            "warn",
            "warning",
            "error",
            "crit",
            "  HIGH  ",
        ],
    )
    def test_is_valid_fail_on_true(self, val: str) -> None:
        assert is_valid_fail_on(val) is True

    @pytest.mark.parametrize("val", ["", "   ", "invalid", "fatal", "pass", "123", "no"])
    def test_is_valid_fail_on_false(self, val: str) -> None:
        assert is_valid_fail_on(val) is False


class TestIssueCategory:
    """Tests for IssueCategory enum values."""

    def test_categories_values(self) -> None:
        assert IssueCategory.AUTOLOAD_CYCLE.value == "autoload_cycle"
        assert IssueCategory.UNUSED_AUTOLOAD.value == "unused_autoload"
        assert IssueCategory.BROKEN_RESOURCE.value == "broken_resource"
        assert IssueCategory.UNUSED_RESOURCE.value == "unused_resource"
        assert IssueCategory.ORPHAN_ASSET.value == "orphan_asset"
        assert IssueCategory.DEAD_FUNCTION.value == "dead_function"
        assert IssueCategory.PERFORMANCE_ANTIPATTERN.value == "performance_antipattern"


class TestAuditIssueAndResult:
    """Tests for AuditIssue and AuditResult dataclasses."""

    def test_audit_issue_to_dict_with_enum_and_str(self) -> None:
        issue1 = AuditIssue(
            category=IssueCategory.AUTOLOAD_CYCLE,
            severity=Severity.HIGH,
            title="Cycle detected",
            description="A -> B -> A",
            file_path="res://a.gd",
            line_number=10,
            column_number=5,
            context="line 10",
        )
        d1 = issue1.to_dict()
        assert d1["category"] == "autoload_cycle"
        assert d1["severity"] == "high"
        assert d1["title"] == "Cycle detected"
        assert d1["file_path"] == "res://a.gd"
        assert d1["line_number"] == 10
        assert d1["column_number"] == 5
        assert d1["context"] == "line 10"

        issue2 = AuditIssue(
            category="custom_arbitrary_category",
            severity=Severity.LOW,
            title="Custom Title",
            description="Custom Description",
        )
        d2 = issue2.to_dict()
        assert d2["category"] == "custom_arbitrary_category"
        assert d2["severity"] == "low"
        assert d2["file_path"] is None
        assert d2["line_number"] is None

    def test_audit_result_filtering_and_counts(self, tmp_path: Path) -> None:
        issues = [
            AuditIssue(category="c1", severity=Severity.CRITICAL, title="t1", description="d1"),
            AuditIssue(category="c2", severity=Severity.HIGH, title="t2", description="d2"),
            AuditIssue(category="c3", severity=Severity.MEDIUM, title="t3", description="d3"),
            AuditIssue(category="c4", severity=Severity.LOW, title="t4", description="d4"),
            AuditIssue(category="c5", severity=Severity.INFO, title="t5", description="d5"),
        ]
        result = AuditResult(
            project_path=tmp_path,
            project_name="TestProject",
            issues=issues,
            file_stats={"gd": 10, "tscn": 5},
            duration_seconds=1.23456,
        )

        # Filtering
        assert len(result.filter_by_severity(Severity.CRITICAL)) == 1
        assert len(result.filter_by_severity(Severity.HIGH)) == 2
        assert len(result.filter_by_severity(Severity.MEDIUM)) == 3
        assert len(result.filter_by_severity(Severity.LOW)) == 4
        assert len(result.filter_by_severity(Severity.INFO)) == 5

        # Counts
        counts = result.counts_by_severity()
        assert counts["critical"] == 1
        assert counts["high"] == 1
        assert counts["medium"] == 1
        assert counts["low"] == 1
        assert counts["info"] == 1

    def test_audit_result_should_fail_combinations(self, tmp_path: Path) -> None:
        issues = [
            AuditIssue(category="c", severity=Severity.MEDIUM, title="t", description="d"),
        ]
        result = AuditResult(project_path=tmp_path, project_name="TestProject", issues=issues)

        # Never values never fail
        assert result.should_fail("never") is False
        assert result.should_fail("NEVER") is False
        assert result.should_fail("none") is False
        assert result.should_fail("off") is False

        # Thresholds
        assert result.should_fail("info") is True
        assert result.should_fail("low") is True
        assert result.should_fail("medium") is True
        assert result.should_fail("high") is False
        assert result.should_fail("critical") is False

        # Empty issues
        empty_result = AuditResult(project_path=tmp_path, project_name="Empty")
        assert empty_result.should_fail("info") is False

        # Invalid fail_on raises ValueError
        with pytest.raises(ValueError):
            result.should_fail("invalid_option")

    def test_audit_result_to_dict_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Case 1: project_path is cwd itself (rel_path == ".")
        cwd = Path.cwd()
        res_cwd = AuditResult(project_path=cwd, project_name="CwdProject")
        dict_cwd = res_cwd.to_dict()
        assert dict_cwd["project"]["path"] == "CwdProject"

        # Case 2: project_path is a subfolder of cwd (rel_path != ".")
        subfolder = cwd / "some_virtual_subdir"
        res_sub = AuditResult(project_path=subfolder, project_name="SubProject")
        dict_sub = res_sub.to_dict()
        assert dict_sub["project"]["path"] == "some_virtual_subdir"
        assert dict_sub["summary"]["total_issues"] == 0

        # Case 3: project_path raises ValueError/RuntimeError on relative_to
        def mock_relative_to(self: Path, other: Path) -> Path:
            raise ValueError("Different drive root")

        monkeypatch.setattr(Path, "relative_to", mock_relative_to)
        res_diff_drive = AuditResult(
            project_path=Path("Z:/other_drive/proj"), project_name="OtherDrive"
        )
        dict_diff = res_diff_drive.to_dict()
        assert dict_diff["project"]["path"] == "OtherDrive"


# ==============================================================================
# PART 2: gdaudit.core.autoloads Helper Unit Tests
# ==============================================================================


class TestAutoloadHelpers:
    """Detailed unit tests for helper functions in core/autoloads.py."""

    def test_strip_comments_all_branches(self) -> None:
        # Simple GDScript code without comments
        code1 = "var x = 10\nvar y = 'hello'"
        assert _strip_comments(code1) == "var x = 10\nvar y = 'hello'"

        # Single line comment
        code2 = "# Full line comment\nvar x = 1 # End of line comment"
        assert _strip_comments(code2) == "\nvar x = 1 "

        # Comments inside double and single quotes must be preserved
        code3 = "var url = \"http://example.com#section\"\nvar tag = '#not_a_comment'"
        assert _strip_comments(code3) == code3

        # Escaped quotes inside strings
        code4 = (
            r'var s1 = "escaped \" double # still string"'
            + "\n"
            + r"var s2 = 'escaped \' single # still string'"
        )
        assert _strip_comments(code4) == code4

        # Escaped backslash inside strings
        code5 = r'var path = "C:\\Windows\\System32" # comment'
        assert _strip_comments(code5) == r'var path = "C:\\Windows\\System32" '

        # Mixed single and double quotes
        code6 = "var mixed1 = \"'#nested'\" # trailing\nvar mixed2 = '\"#nested\"' # trailing"
        assert _strip_comments(code6) == "var mixed1 = \"'#nested'\" \nvar mixed2 = '\"#nested\"' "

    def test_extract_script_paths_from_tscn(self) -> None:
        tscn_content = """
[gd_scene load_steps=5 format=3]

[ext_resource type="Script" path="res://player.gd" id="1_abc"]
[ext_resource type='Script' path='res://enemy.gd' id='2_def']
[ext_resource path="res://reversed_attr.gd" type="Script" id="3_ghi"]
[ext_resource type="PackedScene" path="res://level.tscn" id="4_jkl"]
[ext_resource type="Script" id="5_no_path"]
[node name="Node2D" type="Node2D"]
"""
        extracted = _extract_script_paths_from_tscn(tscn_content)
        assert "res://player.gd" in extracted
        assert "res://enemy.gd" in extracted
        assert "res://reversed_attr.gd" in extracted
        assert "res://level.tscn" not in extracted
        assert len(extracted) == 3

        # Empty or no ext_resource
        assert _extract_script_paths_from_tscn("") == []
        assert _extract_script_paths_from_tscn("[node name='Main' type='Node']") == []

    def test_find_canonical_cycle_edge_cases(self) -> None:
        # Empty or single node
        assert _find_canonical_cycle([]) == ()
        assert _find_canonical_cycle(["A"]) == ()

        # Canonical rotation minimum selection
        c1 = ["B", "C", "A", "B"]
        c2 = ["C", "A", "B", "C"]
        c3 = ["A", "B", "C", "A"]
        assert _find_canonical_cycle(c1) == ("A", "B", "C")
        assert _find_canonical_cycle(c2) == ("A", "B", "C")
        assert _find_canonical_cycle(c3) == ("A", "B", "C")

    def test_get_autoload_source_content_branches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 1. project_or_root passed as Path directly
        script_file = tmp_path / "singleton.gd"
        script_file.write_text("extends Node\n# My singleton", encoding="utf-8")
        entry = AutoloadEntry("MySingleton", "res://singleton.gd")

        content = _get_autoload_source_content(entry, tmp_path)
        assert "My singleton" in content

        # 2. Path traversal ValueError in res_to_path -> returns ""
        bad_entry = AutoloadEntry("BadTraversal", "res://../../escape.gd")
        assert _get_autoload_source_content(bad_entry, tmp_path) == ""

        # 3. Target file does not exist on disk -> returns ""
        missing_entry = AutoloadEntry("Missing", "res://does_not_exist.gd")
        assert _get_autoload_source_content(missing_entry, tmp_path) == ""

        # 4. Target file exists but reading raises OSError -> returns ""
        unreadable_entry = AutoloadEntry("Unreadable", "res://unreadable.gd")
        (tmp_path / "unreadable.gd").write_text("content", encoding="utf-8")

        def mock_read_text(*args: Any, **kwargs: Any) -> str:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "read_text", mock_read_text)
        assert _get_autoload_source_content(unreadable_entry, tmp_path) == ""

    def test_get_autoload_source_content_tscn_attached_scripts(self, tmp_path: Path) -> None:
        # Create a .tscn scene autoload with attached script
        tscn_file = tmp_path / "gui.tscn"
        tscn_file.write_text(
            """[gd_scene format=3]
[ext_resource type="Script" path="res://gui.gd" id="1_gui"]
[ext_resource type="Script" path="res://../../outside.gd" id="2_bad"]
[ext_resource type="Script" path="res://nonexistent.gd" id="3_missing"]
[node name="GUI" type="Control"]
""",
            encoding="utf-8",
        )
        gui_script = tmp_path / "gui.gd"
        gui_script.write_text("extends Control\nfunc show_gui(): pass\n", encoding="utf-8")

        entry = AutoloadEntry("GUI", "res://gui.tscn")
        # When passed as Path (file_texts is empty, falls back to disk read)
        content = _get_autoload_source_content(entry, tmp_path)
        assert '[node name="GUI" type="Control"]' in content
        assert "func show_gui(): pass" in content


# ==============================================================================
# PART 3: AutoloadAnalyzer Comprehensive Integration Tests
# ==============================================================================


class TestAutoloadAnalyzerIntegration:
    """Integration and robustness tests for AutoloadAnalyzer with GodotProject."""

    def test_empty_autoloads_project(self, tmp_path: Path) -> None:
        (tmp_path / "project.godot").write_text(
            """config_version=5
[application]
config/name="NoAutoloadsProject"
""",
            encoding="utf-8",
        )
        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)

        assert analyzer.build_graph() == {}
        assert analyzer.detect_cycles() == []
        assert analyzer.detect_unused_autoloads() == []
        assert analyzer.analyze() == []

    def test_direct_cycle_a_b_a(self, tmp_path: Path) -> None:
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
Alpha="*res://alpha.gd"
Beta="*res://beta.gd"
""",
            encoding="utf-8",
        )
        (tmp_path / "alpha.gd").write_text(
            "extends Node\nfunc _ready(): Beta.ping()\n", encoding="utf-8"
        )
        (tmp_path / "beta.gd").write_text(
            "extends Node\nfunc _ready(): Alpha.pong()\n", encoding="utf-8"
        )

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()

        cycles = [i for i in issues if i.category == IssueCategory.AUTOLOAD_CYCLE]
        assert len(cycles) == 1
        assert cycles[0].severity == Severity.HIGH
        assert "Alpha" in cycles[0].description
        assert "Beta" in cycles[0].description

    def test_indirect_complex_cycle_a_b_c_d_a(self, tmp_path: Path) -> None:
        """Complex multi-node indirect circular dependency (A -> B -> C -> D -> A)."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
NodeA="*res://node_a.gd"
NodeB="*res://node_b.gd"
NodeC="*res://node_c.gd"
NodeD="*res://node_d.gd"
""",
            encoding="utf-8",
        )
        (tmp_path / "node_a.gd").write_text(
            "extends Node\nfunc _ready(): NodeB.step()\n", encoding="utf-8"
        )
        (tmp_path / "node_b.gd").write_text(
            "extends Node\nfunc step(): NodeC.step()\n", encoding="utf-8"
        )
        (tmp_path / "node_c.gd").write_text(
            "extends Node\nfunc step(): NodeD.step()\n", encoding="utf-8"
        )
        (tmp_path / "node_d.gd").write_text(
            "extends Node\nfunc step(): NodeA.step()\n", encoding="utf-8"
        )

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()

        cycles = [i for i in issues if i.category == IssueCategory.AUTOLOAD_CYCLE]
        assert len(cycles) == 1
        desc = cycles[0].description
        for name in ["NodeA", "NodeB", "NodeC", "NodeD"]:
            assert name in desc

    def test_missing_target_script(self, tmp_path: Path) -> None:
        """Autoload configured in project.godot referencing a nonexistent script."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
MissingManager="*res://missing_script.gd"
""",
            encoding="utf-8",
        )
        # Note: missing_script.gd is intentionally NOT created on disk

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()

        # Analyzer should not crash, and MissingManager should be flagged as unused
        unused = [i for i in issues if i.category == IssueCategory.UNUSED_AUTOLOAD]
        assert len(unused) == 1
        assert "MissingManager" in unused[0].title

    def test_autoload_invalid_path_and_no_autoload_section(self, tmp_path: Path) -> None:
        """Project with invalid path in autoload entry and project without [autoload]."""
        # Subcase A: No autoload section
        (tmp_path / "project.godot").write_text(
            """config_version=5
[application]
config/name="CleanApp"
""",
            encoding="utf-8",
        )
        proj_clean = GodotProject(tmp_path)
        analyzer_clean = AutoloadAnalyzer(proj_clean)
        assert analyzer_clean.analyze() == []

        # Subcase B: Invalid path traversal in project.godot
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
TraverseManager="*res://../../outside.gd"
""",
            encoding="utf-8",
        )
        proj_bad = GodotProject(tmp_path)
        analyzer_bad = AutoloadAnalyzer(proj_bad)
        # res_to_path raises ValueError internally, handled gracefully
        issues = analyzer_bad.analyze()
        assert isinstance(issues, list)

    def test_corrupt_project_godot_syntax(self, tmp_path: Path) -> None:
        """Corrupt lines and syntax anomalies in project.godot [autoload] section."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
corrupt_line_without_equal
=broken_prop_without_key
[autoload]
some_invalid_line
ValidService="*res://valid_service.gd"
EmptyTarget=""
""",
            encoding="utf-8",
        )
        (tmp_path / "valid_service.gd").write_text("extends Node\n", encoding="utf-8")

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()
        assert isinstance(issues, list)
        # ValidService is unused
        unused_names = [i.title for i in issues if i.category == IssueCategory.UNUSED_AUTOLOAD]
        assert any("ValidService" in name for name in unused_names)

    def test_dependency_resolution_patterns_and_activation_order(self, tmp_path: Path) -> None:
        """Tests that AutoloadAnalyzer detects dependencies via id, /root/path, and res://."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
ServiceA="*res://service_a.gd"
ServiceB="*res://service_b.gd"
ServiceC="*res://service_c.gd"
ServiceD="*res://service_d.gd"
""",
            encoding="utf-8",
        )

        # ServiceA references ServiceB via global singleton identifier
        (tmp_path / "service_a.gd").write_text(
            """extends Node
func _ready():
    ServiceB.trigger()
""",
            encoding="utf-8",
        )

        # ServiceB references ServiceC via /root/ServiceC scene tree path
        (tmp_path / "service_b.gd").write_text(
            """extends Node
func trigger():
    var c = get_node("/root/ServiceC")
""",
            encoding="utf-8",
        )

        # ServiceC references ServiceD via direct res:// reference
        (tmp_path / "service_c.gd").write_text(
            """extends Node
func action():
    var d = load("res://service_d.gd")
""",
            encoding="utf-8",
        )

        # ServiceD has no outbound dependencies
        (tmp_path / "service_d.gd").write_text("extends Node\n", encoding="utf-8")

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        graph = analyzer.build_graph()

        assert graph["ServiceA"] == {"ServiceB"}
        assert graph["ServiceB"] == {"ServiceC"}
        assert graph["ServiceC"] == {"ServiceD"}
        assert graph["ServiceD"] == set()

        # Acyclic graph -> no circular issues
        assert analyzer.detect_cycles() == []

    def test_scene_autoload_tscn_vs_script_autoload_gd(self, tmp_path: Path) -> None:
        """Autoload declared as a .tscn scene referencing a .gd autoload and vice-versa."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
HudScene="*res://hud.tscn"
AudioManager="*res://audio.gd"
""",
            encoding="utf-8",
        )

        # hud.tscn references hud.gd
        (tmp_path / "hud.tscn").write_text(
            """[gd_scene format=3]
[ext_resource type="Script" path="res://hud.gd" id="1_hud"]
[node name="HUD" type="CanvasLayer"]
""",
            encoding="utf-8",
        )

        # hud.gd calls AudioManager
        (tmp_path / "hud.gd").write_text(
            """extends CanvasLayer
func _ready():
    AudioManager.play_bgm()
""",
            encoding="utf-8",
        )

        # audio.gd calls HudScene -> cycle between .tscn and .gd!
        (tmp_path / "audio.gd").write_text(
            """extends Node
func play_bgm():
    HudScene.show_alert()
""",
            encoding="utf-8",
        )

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()

        cycles = [i for i in issues if i.category == IssueCategory.AUTOLOAD_CYCLE]
        assert len(cycles) == 1
        assert "HudScene" in cycles[0].description
        assert "AudioManager" in cycles[0].description

    def test_unused_scene_autoload_deduplication(self, tmp_path: Path) -> None:
        """A .tscn scene autoload whose attached script references its own name stays unused."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
MenuSingleton="*res://menu.tscn"
""",
            encoding="utf-8",
        )

        (tmp_path / "menu.tscn").write_text(
            """[gd_scene format=3]
[ext_resource type="Script" path="res://menu.gd" id="1_menu"]
[node name="Menu" type="Control"]
""",
            encoding="utf-8",
        )

        # menu.gd references "MenuSingleton" internally, but no external file calls it
        (tmp_path / "menu.gd").write_text(
            """extends Control
func reset():
    # self reference
    print(MenuSingleton)
""",
            encoding="utf-8",
        )

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.analyze()

        unused = [i for i in issues if i.category == IssueCategory.UNUSED_AUTOLOAD]
        assert len(unused) == 1
        assert "MenuSingleton" in unused[0].title

    def test_detect_unused_autoload_with_invalid_paths_and_tscn_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercise branches in detect_unused_autoloads where paths raise ValueError/OSError."""
        (tmp_path / "project.godot").write_text(
            """config_version=5
[autoload]
SceneWithBadExt="*res://bad_ext.tscn"
""",
            encoding="utf-8",
        )

        # .tscn referencing a path that triggers ValueError when resolved
        (tmp_path / "bad_ext.tscn").write_text(
            """[gd_scene format=3]
[ext_resource type="Script" path="res://../../outside.gd" id="1_bad"]
[node name="BadNode" type="Node"]
""",
            encoding="utf-8",
        )

        project = GodotProject(tmp_path)
        analyzer = AutoloadAnalyzer(project)
        issues = analyzer.detect_unused_autoloads()
        # Should execute cleanly through ValueError handling on line 228
        assert len(issues) == 1
        assert "SceneWithBadExt" in issues[0].title
