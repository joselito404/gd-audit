"""Unit tests for reporters (Console, JSON, Markdown, GitHub annotations)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from gdaudit.models import AuditIssue, AuditResult, IssueCategory, Severity
from gdaudit.reporters.console import render_console, render_github_annotations
from gdaudit.reporters.json_reporter import render_json
from gdaudit.reporters.markdown import render_markdown


def create_dummy_result(tmp_path: Path) -> AuditResult:
    issues = [
        AuditIssue(
            category=IssueCategory.AUTOLOAD_CYCLE,
            severity=Severity.HIGH,
            title="Circular Dependency: A -> B -> A",
            description="Mutual autoload reference",
            file_path="manager_a.gd",
            line_number=5,
        ),
        AuditIssue(
            category=IssueCategory.PERFORMANCE_ANTIPATTERN,
            severity=Severity.MEDIUM,
            title="Hot Loop get_node()",
            description="Expensive node lookup in _process",
            file_path="player.gd",
            line_number=20,
        ),
        AuditIssue(
            category=IssueCategory.DEAD_FUNCTION,
            severity=Severity.LOW,
            title="Dead Function: old_helper()",
            description="Unused method",
            file_path="utils.gd",
            line_number=12,
        ),
    ]
    return AuditResult(
        project_path=tmp_path,
        project_name="TestGame",
        issues=issues,
        file_stats={"gd_scripts": 3, "scenes": 1, "resources": 0, "assets": 0},
        duration_seconds=0.042,
    )


def test_console_reporter(tmp_path: Path) -> None:
    res = create_dummy_result(tmp_path)
    console = Console(record=True, width=120)
    render_console(res, min_severity=Severity.LOW, console=console)
    output = console.export_text()

    assert "TestGame" in output
    assert "Circular Dependency" in output
    assert "Hot Loop get_node()" in output


def test_github_annotations(tmp_path: Path) -> None:
    res = create_dummy_result(tmp_path)
    annotations = render_github_annotations(res, min_severity=Severity.MEDIUM)

    assert "::error file=manager_a.gd,line=5" in annotations
    assert "::warning file=player.gd,line=20" in annotations
    # Low severity issue should be excluded when min_severity is MEDIUM
    assert "utils.gd" not in annotations


def test_github_annotations_escaping(tmp_path: Path) -> None:
    issues = [
        AuditIssue(
            category=IssueCategory.PERFORMANCE_ANTIPATTERN,
            severity=Severity.HIGH,
            title="Issue with % and : and , characters",
            description="Multiline\ndescription with % and \r and newline",
            file_path="path:with,commas%20/file.gd",
            line_number=10,
        )
    ]
    res = AuditResult(
        project_path=tmp_path,
        project_name="SpecialGame",
        issues=issues,
    )
    annotations = render_github_annotations(res, min_severity=Severity.HIGH)

    # % should be %25, : should be %3A, , should be %2C
    assert "file=path%3Awith%2Ccommas%2520/file.gd" in annotations
    assert "title=Issue with %25 and %3A and %2C characters" in annotations
    # Description should escape % as %25, \n as %0A, \r as %0D
    assert "Multiline%0Adescription with %25 and %0D and newline" in annotations


def test_json_reporter(tmp_path: Path) -> None:
    res = create_dummy_result(tmp_path)
    out_file = tmp_path / "report.json"
    json_str = render_json(res, min_severity=Severity.LOW, output_path=out_file)

    data = json.loads(json_str)
    assert data["project"]["name"] == "TestGame"
    assert len(data["issues"]) == 3
    assert out_file.exists()


def test_markdown_reporter(tmp_path: Path) -> None:
    res = create_dummy_result(tmp_path)
    out_file = tmp_path / "summary.md"
    md_str = render_markdown(res, min_severity=Severity.LOW, output_path=out_file)

    assert "# 🛡️ gd-audit Report" in md_str
    assert "**Project:** `TestGame`" in md_str
    assert "🔴 **HIGH**" in md_str
    assert out_file.exists()


def test_markdown_reporter_pipe_escaping(tmp_path: Path) -> None:
    issues = [
        AuditIssue(
            category=IssueCategory.BROKEN_RESOURCE,
            severity=Severity.HIGH,
            title="Broken Pipe",
            description="File contains | table pipe characters | inside",
            context="value | with | pipes",
            file_path="broken.tscn",
            line_number=1,
        )
    ]
    res = AuditResult(
        project_path=tmp_path,
        project_name="PipeGame",
        issues=issues,
    )
    md_str = render_markdown(res, min_severity=Severity.HIGH)

    # Pipes inside table cells must be escaped with \|
    assert r"contains \| table pipe characters \| inside" in md_str
    assert r"value \| with \| pipes" in md_str


def test_reporter_path_sanitization_sec03(tmp_path: Path) -> None:
    # A path that is guaranteed to be outside cwd on any operating system
    deep_path = (tmp_path / "secret" / "user" / "project").resolve()
    res = AuditResult(
        project_path=deep_path,
        project_name="SecretProject",
        issues=[],
    )

    # In JSON dict
    data = res.to_dict()
    assert str(deep_path) not in data["project"]["path"]
    assert data["project"]["path"] == "SecretProject"

    # In Console
    console = Console(record=True, width=120)
    render_console(res, console=console)
    console_out = console.export_text()
    assert str(deep_path) not in console_out
    assert "SecretProject" in console_out

    # In Markdown
    md_out = render_markdown(res)
    assert str(deep_path) not in md_out
    assert "SecretProject" in md_out
