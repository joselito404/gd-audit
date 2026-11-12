"""Comprehensive unit tests for configuration loading (.gd-audit.toml) and baseline filtering."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gdaudit.cli import app
from gdaudit.config import (
    AuditConfig,
    filter_by_baseline,
    filter_issues,
    issue_fingerprint,
    issue_matches_reviewed,
    issue_tuple,
    load_baseline,
    load_config,
    normalize_rel_path,
    save_baseline,
)
from gdaudit.models import AuditIssue, IssueCategory, Severity

runner = CliRunner()


# ==============================================================================
# Helper functions
# ==============================================================================


def _setup_godot_project(
    root: Path, name: str = "TestGame", main_scene: str = "res://main.tscn"
) -> Path:
    """Create a minimal valid Godot project structure."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        f"""config_version=5
[application]
config/name="{name}"
run/main_scene="{main_scene}"
""",
        encoding="utf-8",
    )
    (root / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    return root


# ==============================================================================
# 1. Early CLI argument validation (BEFORE discovering/scanning project)
# ==============================================================================


def test_cli_invalid_severity_fails_before_scanning(tmp_path: Path) -> None:
    """Invalid --severity exits with code 2 immediately, even on nonexistent project path."""
    nonexistent = tmp_path / "does_not_exist_at_all"

    result = runner.invoke(app, ["scan", str(nonexistent), "--severity", "super_critical"])

    assert result.exit_code == 2
    assert "Invalid severity:" in result.output
    normalized_output = " ".join(result.output.split())
    assert "Choose from info, low, medium, high, critical." in normalized_output
    # Must NOT have attempted project discovery
    assert "Could not locate 'project.godot'" not in result.output
    assert "Error:" not in result.output


def test_cli_invalid_format_fails_before_scanning(tmp_path: Path) -> None:
    """Invalid --format exits with code 2 immediately, even on nonexistent project path."""
    nonexistent = tmp_path / "does_not_exist_at_all"

    result = runner.invoke(app, ["scan", str(nonexistent), "--format", "pdf"])

    assert result.exit_code == 2
    assert "Invalid format:" in result.output
    normalized_output = " ".join(result.output.split())
    assert "Choose from rich, json, markdown, github." in normalized_output
    # Must NOT have attempted project discovery
    assert "Could not locate 'project.godot'" not in result.output
    assert "Error:" not in result.output


def test_cli_invalid_fail_on_fails_before_scanning(tmp_path: Path) -> None:
    """Invalid --fail-on exits with code 2 immediately, even on nonexistent project path."""
    nonexistent = tmp_path / "does_not_exist_at_all"

    result = runner.invoke(app, ["scan", str(nonexistent), "--fail-on", "catastrophic"])

    assert result.exit_code == 2
    assert "Invalid --fail-on:" in result.output
    normalized_output = " ".join(result.output.split())
    assert "Choose from info, low, medium, high, critical, never." in normalized_output
    # Must NOT have attempted project discovery
    assert "Could not locate 'project.godot'" not in result.output
    assert "Error:" not in result.output


def test_cli_valid_early_options_pass_to_scan(tmp_path: Path) -> None:
    """Valid --severity and --format pass early validation and proceed to scan."""
    _setup_godot_project(tmp_path)

    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--severity", "info", "--format", "json", "--fail-on", "never"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["project"]["name"] == "TestGame"


# ==============================================================================
# 2. Configuration loading and unit tests (.gd-audit.toml)
# ==============================================================================


def test_load_config_nonexistent(tmp_path: Path) -> None:
    """load_config returns default empty config if .gd-audit.toml does not exist."""
    cfg = load_config(tmp_path)
    assert cfg.ignore == []
    assert cfg.entry_scenes == []
    assert cfg.known_dynamic == []
    assert cfg.reviewed == []


def test_load_config_valid_toml(tmp_path: Path) -> None:
    """load_config parses all supported fields from .gd-audit.toml correctly."""
    toml_content = """
ignore = ["legacy/**", "temp/*.gd"]
entry_scenes = ["scenes/level1.tscn", "res://scenes/splash.tscn"]
known_dynamic = ["on_custom_event", "handle_network_packet"]
reviewed = [
    { file = "scenes/broken.tscn", category = "broken_resource" },
    { path = "scripts/legacy.gd", title = "Dead Function Candidate: 'old_func'" },
    "orphan_asset: assets/icon.png",
]
"""
    (tmp_path / ".gd-audit.toml").write_text(toml_content, encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.ignore == ["legacy/**", "temp/*.gd"]
    assert cfg.entry_scenes == ["scenes/level1.tscn", "res://scenes/splash.tscn"]
    assert cfg.known_dynamic == ["on_custom_event", "handle_network_packet"]
    assert len(cfg.reviewed) == 3
    assert isinstance(cfg.reviewed[0], dict)
    assert cfg.reviewed[0]["file"] == "scenes/broken.tscn"
    assert cfg.reviewed[2] == "orphan_asset: assets/icon.png"


def test_load_config_nested_section(tmp_path: Path) -> None:
    """load_config also parses keys nested under [audit] table."""
    toml_content = """
[audit]
ignore = ["vendor/**"]
entry_scenes = ["main.tscn"]
known_dynamic = ["_on_remote_call"]
reviewed = ["legacy.gd"]
"""
    (tmp_path / ".gd-audit.toml").write_text(toml_content, encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.ignore == ["vendor/**"]
    assert cfg.entry_scenes == ["main.tscn"]
    assert cfg.known_dynamic == ["_on_remote_call"]
    assert cfg.reviewed == ["legacy.gd"]


def test_load_config_corrupt_toml(tmp_path: Path) -> None:
    """load_config safely returns default empty config if TOML is malformed."""
    (tmp_path / ".gd-audit.toml").write_text("invalid = [toml unclosed", encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.ignore == []
    assert cfg.entry_scenes == []


def test_normalize_rel_path() -> None:
    """normalize_rel_path handles backslashes, res:// and leading slashes."""
    assert normalize_rel_path(None) == ""
    assert normalize_rel_path("") == ""
    assert normalize_rel_path("res://scenes/main.tscn") == "scenes/main.tscn"
    assert normalize_rel_path("scenes\\level\\main.tscn") == "scenes/level/main.tscn"
    assert normalize_rel_path("./scripts/actor.gd") == "scripts/actor.gd"


# ==============================================================================
# 3. Filtering by .gd-audit.toml (ignore, entry_scenes, known_dynamic, reviewed)
# ==============================================================================


def test_config_ignore_filters_files_from_discovery(tmp_path: Path) -> None:
    """Files matching config 'ignore' are not discovered or audited."""
    _setup_godot_project(tmp_path)

    # Ignored directory with a broken resource
    ignored_dir = tmp_path / "ignored_folder"
    ignored_dir.mkdir()
    (ignored_dir / "broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing_script.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )

    # Configure .gd-audit.toml to ignore ignored_folder
    (tmp_path / ".gd-audit.toml").write_text('ignore = ["ignored_folder/**"]\n', encoding="utf-8")

    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    # The broken resource in ignored_folder should not be present
    assert data["summary"]["total_issues"] == 0


def test_config_entry_scenes_suppresses_orphan_asset(tmp_path: Path) -> None:
    """Scenes listed in config 'entry_scenes' are not reported as orphan_asset."""
    _setup_godot_project(tmp_path)

    # Create an unreferenced scene
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "entry_level.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (scenes_dir / "orphan_level.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    # Configure .gd-audit.toml with entry_level as entry scene
    (tmp_path / ".gd-audit.toml").write_text(
        'entry_scenes = ["scenes/entry_level.tscn"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json", "--severity", "low"])
    assert result.exit_code == 0
    data = json.loads(result.output)

    reported_files = [i["file_path"] for i in data["issues"]]
    # orphan_level.tscn must be reported
    assert any("orphan_level.tscn" in f for f in reported_files)
    # entry_level.tscn must NOT be reported as orphan
    assert not any("entry_level.tscn" in f for f in reported_files)


def test_config_known_dynamic_suppresses_dead_function(tmp_path: Path) -> None:
    """Functions listed in config 'known_dynamic' are not reported as dead_function."""
    _setup_godot_project(tmp_path)

    (tmp_path / "handlers.gd").write_text(
        """extends Node

func on_network_packet():
    pass

func completely_dead():
    pass
""",
        encoding="utf-8",
    )

    # Configure .gd-audit.toml with on_network_packet in known_dynamic
    (tmp_path / ".gd-audit.toml").write_text(
        'known_dynamic = ["on_network_packet"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json", "--severity", "low"])
    assert result.exit_code == 0
    data = json.loads(result.output)

    reported_titles = [i["title"] for i in data["issues"]]
    # completely_dead must be reported as dead function
    assert any("completely_dead" in t for t in reported_titles)
    # on_network_packet must NOT be reported as dead function
    assert not any("on_network_packet" in t for t in reported_titles)


def test_config_reviewed_suppresses_accepted_findings(tmp_path: Path) -> None:
    """Findings matching entries in config 'reviewed' are suppressed."""
    _setup_godot_project(tmp_path)

    # Create broken resource
    (tmp_path / "reviewed_broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )
    (tmp_path / "unreviewed_broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing2.gd" id="1_xyz"]
[node name="Node" type="Node"]
script = ExtResource("1_xyz")
""",
        encoding="utf-8",
    )

    # Mark reviewed_broken.tscn as reviewed
    (tmp_path / ".gd-audit.toml").write_text(
        """reviewed = [
    { file = "reviewed_broken.tscn", category = "broken_resource" }
]
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--format",
            "json",
            "--severity",
            "low",
            "--fail-on",
            "never",
            "--no-check-orphans",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)

    reported_files = [i["file_path"] for i in data["issues"]]
    assert "unreviewed_broken.tscn" in reported_files
    assert "reviewed_broken.tscn" not in reported_files


def test_issue_matches_reviewed_various_formats() -> None:
    """issue_matches_reviewed matches dicts with file/title/category and string patterns."""
    issue = AuditIssue(
        category=IssueCategory.BROKEN_RESOURCE,
        severity=Severity.HIGH,
        title="Broken ExtResource: missing.tres",
        description="Missing resource reference",
        file_path="scenes/player.tscn",
        line_number=10,
    )

    # 1. Matching dicts
    assert issue_matches_reviewed(issue, {"file": "scenes/player.tscn"})
    assert issue_matches_reviewed(
        issue, {"file": "scenes/player.tscn", "category": "broken_resource"}
    )
    assert issue_matches_reviewed(issue, {"path": "scenes/player.tscn", "title": "missing.tres"})
    assert issue_matches_reviewed(issue, {"line": 10, "file": "scenes/player.tscn"})

    # 2. Non-matching dicts
    assert not issue_matches_reviewed(issue, {"file": "other.tscn"})
    assert not issue_matches_reviewed(
        issue, {"file": "scenes/player.tscn", "category": "orphan_asset"}
    )
    assert not issue_matches_reviewed(issue, {"file": "scenes/player.tscn", "line": 99})

    # 3. Matching strings
    assert issue_matches_reviewed(issue, "scenes/player.tscn")
    assert issue_matches_reviewed(issue, "Broken ExtResource: missing.tres")
    assert issue_matches_reviewed(issue, "broken_resource: scenes/player.tscn")

    # 4. Non-matching strings
    assert not issue_matches_reviewed(issue, "scenes/other.tscn")
    assert not issue_matches_reviewed(issue, "orphan_asset: scenes/player.tscn")


def test_filter_issues_unit() -> None:
    """filter_issues filters issues according to AuditConfig rules."""
    config = AuditConfig(
        ignore=["temp/**"],
        entry_scenes=["scenes/entry.tscn"],
        known_dynamic=["my_dynamic_func"],
        reviewed=[{"file": "known_issue.gd"}],
    )

    issues = [
        AuditIssue(
            category=IssueCategory.BROKEN_RESOURCE,
            severity=Severity.HIGH,
            title="Broken resource",
            description="",
            file_path="temp/test.tscn",
        ),
        AuditIssue(
            category=IssueCategory.ORPHAN_ASSET,
            severity=Severity.LOW,
            title="Orphan Asset Candidate: scenes/entry.tscn",
            description="",
            file_path="scenes/entry.tscn",
        ),
        AuditIssue(
            category=IssueCategory.DEAD_FUNCTION,
            severity=Severity.LOW,
            title="Dead Function Candidate: 'my_dynamic_func'",
            description="",
            file_path="scripts/handler.gd",
            context="func my_dynamic_func()",
        ),
        AuditIssue(
            category=IssueCategory.DEAD_FUNCTION,
            severity=Severity.LOW,
            title="Dead Function Candidate: 'reviewed_fn'",
            description="",
            file_path="known_issue.gd",
        ),
        AuditIssue(
            category=IssueCategory.BROKEN_RESOURCE,
            severity=Severity.HIGH,
            title="Real broken resource",
            description="",
            file_path="scenes/real.tscn",
        ),
    ]

    filtered = filter_issues(issues, config)
    assert len(filtered) == 1
    assert filtered[0].file_path == "scenes/real.tscn"


# ==============================================================================
# 4. Baseline creation and filtering (--baseline)
# ==============================================================================


def test_baseline_creation_when_absent(tmp_path: Path) -> None:
    """When baseline file is absent, it is created and scan exits with code 0."""
    proj = _setup_godot_project(tmp_path / "MyProject")

    # Create a broken resource (which would normally trigger exit 1 with --fail-on high)
    (proj / "broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://absent.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )

    baseline_file = tmp_path / "baseline.json"
    assert not baseline_file.exists()

    result = runner.invoke(
        app, ["scan", str(proj), "--baseline", str(baseline_file), "--fail-on", "high"]
    )

    # Must complete successfully with exit 0
    assert result.exit_code == 0
    # Must report that baseline was generated
    assert "Baseline generated" in result.output or "baseline.json" in result.output
    # Baseline file must now exist and contain valid JSON with recorded issues
    assert baseline_file.exists()

    content = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert content["version"] == 1
    assert content["issue_count"] >= 1
    recorded = content["issues"]
    assert any("broken.tscn" in i["file_path"] for i in recorded)
    assert any(i["category"] == "broken_resource" for i in recorded)


def test_baseline_filters_existing_issues_when_present(tmp_path: Path) -> None:
    """When baseline file exists, preexisting issues are filtered and only new ones reported."""
    proj = _setup_godot_project(tmp_path / "MyProject")

    # Issue 1: broken resource
    (proj / "broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://absent.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )

    baseline_file = tmp_path / "baseline.json"

    # 1st run: create baseline
    run1 = runner.invoke(app, ["scan", str(proj), "--baseline", str(baseline_file)])
    assert run1.exit_code == 0
    assert baseline_file.exists()

    # 2nd run: baseline exists and project unchanged -> 0 issues reported
    run2 = runner.invoke(
        app, ["scan", str(proj), "--baseline", str(baseline_file), "--format", "json"]
    )
    assert run2.exit_code == 0
    data2 = json.loads(run2.output)
    assert data2["summary"]["total_issues"] == 0

    # 3rd run: introduce a NEW broken resource
    (proj / "second_broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing_second.gd" id="2_xyz"]
[node name="Node" type="Node"]
script = ExtResource("2_xyz")
""",
        encoding="utf-8",
    )

    run3 = runner.invoke(
        app,
        [
            "scan",
            str(proj),
            "--baseline",
            str(baseline_file),
            "--format",
            "json",
            "--fail-on",
            "high",
            "--no-check-orphans",
        ],
    )
    # New high issue must trigger exit code 1
    assert run3.exit_code == 1
    data3 = json.loads(run3.output)
    # Only the new broken_resource issue should be present, broken.tscn is filtered
    assert len(data3["issues"]) == 1
    assert "second_broken.tscn" in data3["issues"][0]["file_path"]
    assert data3["issues"][0]["category"] == "broken_resource"
    assert "second_broken.tscn" in data3["issues"][0]["file_path"]


def test_baseline_io_unit_functions(tmp_path: Path) -> None:
    """Unit tests for save_baseline, load_baseline, issue_fingerprint, and filter_by_baseline."""
    baseline_file = tmp_path / "sub" / "base.json"

    issue1 = AuditIssue(
        category=IssueCategory.BROKEN_RESOURCE,
        severity=Severity.HIGH,
        title="Broken ExtResource: missing.tres",
        description="Missing",
        file_path="scenes/a.tscn",
        line_number=5,
    )
    issue2 = AuditIssue(
        category=IssueCategory.DEAD_FUNCTION,
        severity=Severity.LOW,
        title="Dead Function Candidate: 'old'",
        description="Unused",
        file_path="scripts/b.gd",
        line_number=20,
    )

    # Save baseline
    save_baseline(baseline_file, [issue1])
    assert baseline_file.is_file()

    # Load baseline
    tuples, fps = load_baseline(baseline_file)
    assert issue_tuple(issue1) in tuples
    assert issue_fingerprint(issue1) in fps
    assert issue_tuple(issue2) not in tuples

    # Filter by baseline
    remaining = filter_by_baseline([issue1, issue2], tuples, fps)
    assert len(remaining) == 1
    assert remaining[0] == issue2
