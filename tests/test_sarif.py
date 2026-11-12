"""Tests for SARIF 2.1.0 reporter and CLI integration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gdaudit import __version__
from gdaudit.cli import app
from gdaudit.models import AuditIssue, AuditResult, IssueCategory, Severity
from gdaudit.reporters.sarif import (
    INFORMATION_URI,
    SARIF_SCHEMA,
    SARIF_VERSION,
    render_sarif,
)

runner = CliRunner()


def _create_sample_result(tmp_path: Path) -> AuditResult:
    """Create a sample AuditResult with diverse issues across severity ranks."""
    issues = [
        AuditIssue(
            category=IssueCategory.AUTOLOAD_CYCLE,
            severity=Severity.HIGH,
            title="Circular Dependency: A -> B -> A",
            description="Mutual autoload reference between singletons.",
            file_path="autoloads/manager_a.gd",
            line_number=15,
        ),
        AuditIssue(
            category=IssueCategory.PERFORMANCE_ANTIPATTERN,
            severity=Severity.MEDIUM,
            title="Hot Loop get_node()",
            description="Frequent node resolution inside _process loop.",
            file_path="player/player.gd",
            line_number=42,
        ),
        AuditIssue(
            category=IssueCategory.DEAD_FUNCTION,
            severity=Severity.LOW,
            title="Dead Function: old_helper()",
            description="Function defined but never invoked.",
            file_path="utils/math.gd",
            line_number=8,
        ),
    ]
    return AuditResult(
        project_path=tmp_path,
        project_name="SampleGame",
        issues=issues,
        file_stats={"gd_scripts": 3, "scenes": 1, "resources": 0, "assets": 0},
        duration_seconds=0.035,
    )


def _create_clean_project(root: Path, name: str = "CleanProject") -> Path:
    """Create minimal valid Godot project."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        f"""config_version=5
[application]
config/name="{name}"
run/main_scene="res://main.tscn"
""",
        encoding="utf-8",
    )
    (root / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    return root


def _create_broken_project(root: Path, name: str = "BrokenProject") -> Path:
    """Create Godot project containing a broken resource (HIGH severity)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        f"""config_version=5
[application]
config/name="{name}"
run/main_scene="res://main.tscn"
""",
        encoding="utf-8",
    )
    (root / "main.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://missing_script.gd" id="1_missing"]
[node name="Node" type="Node"]
script = ExtResource("1_missing")
""",
        encoding="utf-8",
    )
    return root


# ==============================================================================
# 1. Structure and Schema Verification
# ==============================================================================


def test_render_sarif_basic_structure(tmp_path: Path) -> None:
    """Verify SARIF output conforms to SARIF 2.1.0 root structure."""
    result = _create_sample_result(tmp_path)
    sarif_str = render_sarif(result, min_severity=Severity.LOW)

    data = json.loads(sarif_str)
    assert data["$schema"] == SARIF_SCHEMA
    assert data["version"] == SARIF_VERSION
    assert len(data["runs"]) == 1

    run = data["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "gd-audit"
    assert driver["semanticVersion"] == __version__
    assert driver["informationUri"] == INFORMATION_URI

    # 3 unique issue categories -> 3 rules
    rules = driver["rules"]
    assert len(rules) == 3
    rule_ids = {r["id"] for r in rules}
    assert "autoload_cycle" in rule_ids
    assert "performance_antipattern" in rule_ids
    assert "dead_function" in rule_ids

    # Check rule attributes
    for r in rules:
        assert isinstance(r["id"], str)
        assert isinstance(r["name"], str)
        assert isinstance(r["shortDescription"]["text"], str)
        assert r["defaultConfiguration"]["level"] in ("error", "warning", "note")

    # Check results
    results = run["results"]
    assert len(results) == 3
    for res in results:
        assert res["ruleId"] in rule_ids
        assert res["level"] in ("error", "warning", "note")
        assert "text" in res["message"]
        locs = res["locations"]
        assert len(locs) == 1
        phys = locs[0]["physicalLocation"]
        assert phys["artifactLocation"]["uriBaseId"] == "%SRCROOT%"
        assert isinstance(phys["artifactLocation"]["uri"], str)
        assert phys["region"]["startLine"] >= 1


# ==============================================================================
# 2. Severity Level Mapping
# ==============================================================================


@pytest.mark.parametrize(
    ("severity", "expected_level"),
    [
        (Severity.CRITICAL, "error"),
        (Severity.HIGH, "error"),
        (Severity.MEDIUM, "warning"),
        (Severity.LOW, "warning"),
        (Severity.INFO, "note"),
    ],
)
def test_render_sarif_severity_level_mapping(
    tmp_path: Path, severity: Severity, expected_level: str
) -> None:
    """Verify CRITICAL/HIGH -> error, MEDIUM/LOW -> warning, INFO -> note."""
    issue = AuditIssue(
        category=IssueCategory.BROKEN_RESOURCE,
        severity=severity,
        title="Test Issue",
        description="Severity mapping test description.",
        file_path="test_scene.tscn",
        line_number=1,
    )
    result = AuditResult(
        project_path=tmp_path,
        project_name="SeverityTest",
        issues=[issue],
    )

    sarif_str = render_sarif(result, min_severity=Severity.INFO)
    data = json.loads(sarif_str)

    run = data["runs"][0]
    assert len(run["results"]) == 1
    assert run["results"][0]["level"] == expected_level

    # Check rule defaultConfiguration level
    assert len(run["tool"]["driver"]["rules"]) == 1
    assert run["tool"]["driver"]["rules"][0]["defaultConfiguration"]["level"] == expected_level


# ==============================================================================
# 3. Minimum Severity Filtering
# ==============================================================================


def test_render_sarif_min_severity_filter(tmp_path: Path) -> None:
    """Verify issues below min_severity threshold are filtered out."""
    result = _create_sample_result(tmp_path)

    # Filtering by HIGH should only keep HIGH (autoload_cycle)
    sarif_high = json.loads(render_sarif(result, min_severity=Severity.HIGH))
    results_high = sarif_high["runs"][0]["results"]
    assert len(results_high) == 1
    assert results_high[0]["ruleId"] == "autoload_cycle"
    assert results_high[0]["level"] == "error"

    # Rules in driver should also reflect only active filtered issues
    rules_high = sarif_high["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules_high) == 1
    assert rules_high[0]["id"] == "autoload_cycle"

    # Filtering by MEDIUM should include HIGH and MEDIUM (2 issues)
    sarif_med = json.loads(render_sarif(result, min_severity=Severity.MEDIUM))
    results_med = sarif_med["runs"][0]["results"]
    assert len(results_med) == 2


# ==============================================================================
# 4. Empty Project Handling
# ==============================================================================


def test_render_sarif_empty_project(tmp_path: Path) -> None:
    """Verify project with 0 issues outputs valid SARIF with empty results."""
    result = AuditResult(
        project_path=tmp_path,
        project_name="EmptyGame",
        issues=[],
    )
    sarif_str = render_sarif(result)
    data = json.loads(sarif_str)

    assert data["version"] == "2.1.0"
    assert len(data["runs"]) == 1
    assert data["runs"][0]["results"] == []
    assert data["runs"][0]["tool"]["driver"]["rules"] == []


# ==============================================================================
# 5. File Output and Encoding
# ==============================================================================


def test_render_sarif_file_output(tmp_path: Path) -> None:
    """Verify render_sarif writes formatted JSON to output_path with UTF-8."""
    result = _create_sample_result(tmp_path)
    # Add issue with non-ascii characters to ensure UTF-8 encoding
    result.issues.append(
        AuditIssue(
            category=IssueCategory.UNUSED_RESOURCE,
            severity=Severity.LOW,
            title="Recurso no utilizado: corazón.tres",
            description="El archivo contiene caracteres especiales: á, é, í, ó, ú, ñ.",
            file_path="recursos/corazón.tres",
            line_number=1,
        )
    )

    out_file = tmp_path / "reports" / "audit.sarif"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    returned_str = render_sarif(result, output_path=out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert content == returned_str
    assert "corazón.tres" in content
    assert "á, é, í, ó, ú, ñ" in content

    # Verify indentation
    assert '\n  "$schema":' in content


# ==============================================================================
# 6. Location Handling and Edge Cases
# ==============================================================================


def test_render_sarif_location_handling(tmp_path: Path) -> None:
    """Verify fallback handling for None file_path, missing line numbers, and backslashes."""
    issues = [
        AuditIssue(
            category=IssueCategory.AUTOLOAD_CYCLE,
            severity=Severity.HIGH,
            title="Missing Path Issue",
            description="Issue without specific file path.",
            file_path=None,
            line_number=None,
        ),
        AuditIssue(
            category=IssueCategory.BROKEN_RESOURCE,
            severity=Severity.HIGH,
            title="Windows Backslash Issue",
            description="Issue with Windows path separator.",
            file_path=r"nested\scripts\component.gd",
            line_number=0,
        ),
    ]
    result = AuditResult(
        project_path=tmp_path,
        project_name="EdgeLocationGame",
        issues=issues,
    )

    data = json.loads(render_sarif(result, min_severity=Severity.LOW))
    results = data["runs"][0]["results"]

    # Issue 1: file_path=None -> defaults to "project.godot", line_number=None -> 1
    loc0 = results[0]["locations"][0]["physicalLocation"]
    assert loc0["artifactLocation"]["uri"] == "project.godot"
    assert loc0["region"]["startLine"] == 1

    # Issue 2: backslashes replaced with forward slashes, line_number=0 -> 1
    loc1 = results[1]["locations"][0]["physicalLocation"]
    assert loc1["artifactLocation"]["uri"] == "nested/scripts/component.gd"
    assert loc1["region"]["startLine"] == 1


def test_render_sarif_custom_category(tmp_path: Path) -> None:
    """Verify custom string category generates a valid rule in driver."""
    issue = AuditIssue(
        category="security_risk",
        severity=Severity.CRITICAL,
        title="Insecure Storage",
        description="Plaintext password saved in config file.",
        file_path="config/settings.cfg",
        line_number=5,
    )
    result = AuditResult(
        project_path=tmp_path,
        project_name="CustomCategoryGame",
        issues=[issue],
    )

    data = json.loads(render_sarif(result, min_severity=Severity.LOW))
    run = data["runs"][0]

    rules = run["tool"]["driver"]["rules"]
    assert len(rules) == 1
    assert rules[0]["id"] == "security_risk"
    assert rules[0]["name"] == "SecurityRisk"
    assert rules[0]["defaultConfiguration"]["level"] == "error"


# ==============================================================================
# 7. Strict JSON Validation
# ==============================================================================


def test_render_sarif_strict_json_validation(tmp_path: Path) -> None:
    """Validate all types, keys, and values strictly against SARIF 2.1.0 specifications."""
    result = _create_sample_result(tmp_path)
    sarif_str = render_sarif(result)

    # Must parse with strict=True
    doc: dict[str, Any] = json.loads(sarif_str, strict=True)

    # 1. Root requirements
    assert doc["$schema"] == SARIF_SCHEMA
    assert doc["version"] == "2.1.0"
    assert isinstance(doc["runs"], list)
    assert len(doc["runs"]) == 1

    run = doc["runs"][0]
    assert isinstance(run, dict)

    # 2. Tool / Driver requirements
    tool = run["tool"]
    driver = tool["driver"]
    assert driver["name"] == "gd-audit"
    assert isinstance(driver["semanticVersion"], str)
    assert isinstance(driver["informationUri"], str)
    assert isinstance(driver["rules"], list)

    for rule in driver["rules"]:
        assert isinstance(rule["id"], str) and len(rule["id"]) > 0
        assert isinstance(rule["name"], str) and len(rule["name"]) > 0
        assert isinstance(rule["shortDescription"], dict)
        assert isinstance(rule["shortDescription"]["text"], str)
        assert isinstance(rule["defaultConfiguration"], dict)
        assert rule["defaultConfiguration"]["level"] in ("error", "warning", "note", "none")

    # 3. Results requirements
    assert isinstance(run["results"], list)
    for res in run["results"]:
        assert isinstance(res["ruleId"], str)
        assert res["level"] in ("error", "warning", "note", "none")
        assert isinstance(res["message"], dict)
        assert isinstance(res["message"]["text"], str) and len(res["message"]["text"]) > 0
        assert isinstance(res["locations"], list) and len(res["locations"]) > 0
        for loc in res["locations"]:
            phys = loc["physicalLocation"]
            assert isinstance(phys["artifactLocation"]["uri"], str)
            assert phys["artifactLocation"]["uriBaseId"] == "%SRCROOT%"
            assert isinstance(phys["region"]["startLine"], int)
            assert phys["region"]["startLine"] >= 1


# ==============================================================================
# 8. CLI Integration Tests
# ==============================================================================


def test_cli_scan_sarif_stdout(tmp_path: Path) -> None:
    """Verify CLI scan with --format sarif outputs valid SARIF JSON to stdout."""
    project_dir = _create_clean_project(tmp_path / "SarifCliClean")

    result = runner.invoke(app, ["scan", str(project_dir), "--format", "sarif"])
    assert result.exit_code == 0

    parsed = json.loads(result.output)
    assert parsed["version"] == "2.1.0"
    assert parsed["runs"][0]["tool"]["driver"]["name"] == "gd-audit"
    assert parsed["runs"][0]["results"] == []


def test_cli_scan_sarif_output_file(tmp_path: Path) -> None:
    """Verify CLI scan with --format sarif and --output writes file without stdout dump."""
    project_dir = _create_clean_project(tmp_path / "SarifCliFile")
    out_file = tmp_path / "output.sarif"

    result = runner.invoke(
        app,
        ["scan", str(project_dir), "--format", "sarif", "--output", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.exists()

    # Stdout should not contain the JSON document
    assert "https://raw.githubusercontent.com" not in result.output

    # File should contain valid SARIF JSON
    parsed = json.loads(out_file.read_text(encoding="utf-8"))
    assert parsed["version"] == "2.1.0"


def test_cli_scan_sarif_with_broken_project(tmp_path: Path) -> None:
    """Verify CLI scan with --format sarif identifies broken resource as error."""
    project_dir = _create_broken_project(tmp_path / "SarifCliBroken")

    result = runner.invoke(
        app,
        ["scan", str(project_dir), "--format", "sarif", "--fail-on", "never"],
    )
    assert result.exit_code == 0

    parsed = json.loads(result.output)
    results = parsed["runs"][0]["results"]
    assert len(results) >= 1

    broken_res = next(r for r in results if r["ruleId"] == "broken_resource")
    assert broken_res["level"] == "error"
    assert "missing_script.gd" in broken_res["message"]["text"]


def test_cli_sarif_cp1252_console(tmp_path: Path) -> None:
    """Verify SARIF output survives a legacy Windows cp1252 console."""
    project_dir = _create_clean_project(tmp_path / "SarifCp1252")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)

    proc = subprocess.run(
        [sys.executable, "-m", "gdaudit.cli", "scan", str(project_dir), "--format", "sarif"],
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert b"UnicodeEncodeError" not in proc.stderr
    parsed = json.loads(proc.stdout.decode("utf-8"))
    assert parsed["version"] == "2.1.0"
