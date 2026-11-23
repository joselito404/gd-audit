"""Unit tests for the Typer CLI application."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gdaudit import __version__
from gdaudit.cli import app, configure_utf8_output

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_scan_clean_project(tmp_path: Path) -> None:
    # Create clean project
    (tmp_path / "project.godot").write_text(
        """config_version=5
[application]
config/name="CleanGame"
run/main_scene="res://main.tscn"
""",
        encoding="utf-8",
    )
    (tmp_path / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0
    assert "All checks passed" in result.output or "CleanGame" in result.output


def test_cli_scan_fail_on_threshold(tmp_path: Path) -> None:
    # Create project with broken resource (HIGH severity)
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "broken.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://non_existent.gd" id="1_abc"]
[node name="Node" type="Node"]
script = ExtResource("1_abc")
""",
        encoding="utf-8",
    )

    # Should fail when --fail-on high
    res_fail = runner.invoke(app, ["scan", str(tmp_path), "--fail-on", "high"])
    assert res_fail.exit_code == 1

    # Should pass when --fail-on never
    res_pass = runner.invoke(app, ["scan", str(tmp_path), "--fail-on", "never"])
    assert res_pass.exit_code == 0


def test_cli_scan_json_format(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0

    parsed = json.loads(result.output)
    assert "project" in parsed
    assert "summary" in parsed


def _make_clean_project(root: Path) -> None:
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (root / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")


@pytest.mark.parametrize("fmt", ["markdown", "json", "github"])
def test_cli_screen_output_survives_cp1252_console(tmp_path: Path, fmt: str) -> None:
    """Simulate a legacy Windows console (cp1252, strict): the emoji report must not crash."""
    _make_clean_project(tmp_path)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)

    proc = subprocess.run(
        [sys.executable, "-m", "gdaudit.cli", "scan", str(tmp_path), "--format", fmt],
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert b"UnicodeEncodeError" not in proc.stderr
    if fmt == "markdown":
        assert "🛡".encode() in proc.stdout


def test_configure_utf8_output_reconfigures_legacy_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    legacy = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", legacy)

    configure_utf8_output()
    print("🎉 ok")
    legacy.flush()

    assert buffer.getvalue().decode("utf-8").strip() == "🎉 ok"


def test_configure_utf8_output_ignores_streams_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", object())
    configure_utf8_output()  # must not raise


@pytest.mark.parametrize(
    "value", ["high", "HIGH", " medium ", "warning", "error", "never", "none", "off"]
)
def test_cli_fail_on_accepts_valid_values(tmp_path: Path, value: str) -> None:
    _make_clean_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--fail-on", value])

    assert result.exit_code == 0


def test_cli_invalid_fail_on_exits_2_without_traceback(tmp_path: Path) -> None:
    _make_clean_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--fail-on", "foo"])

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "Invalid --fail-on" in result.output
    assert "info, low, medium, high, critical, never" in result.output


def test_cli_short_help_flag() -> None:
    """Root command and scan subcommand accept -h as a help alias."""
    root_res = runner.invoke(app, ["-h"])
    assert root_res.exit_code == 0
    assert "Usage:" in root_res.output

    scan_res = runner.invoke(app, ["scan", "-h"])
    assert scan_res.exit_code == 0
    assert "--format" in scan_res.output


def test_cli_invalid_format_message_not_duplicated(tmp_path: Path) -> None:
    """Invalid --format lists all five formats once without contradictory duplicates."""
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "xml"])
    assert result.exit_code == 2
    assert "rich, json, markdown, github, sarif" in result.output
    assert result.output.count("Choose from") == 1


def test_cli_invalid_config_value_error_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ValueError while loading project config prints an escaped error and exits with code 2."""

    def _raise_config_error(*args: object, **kwargs: object) -> None:
        raise ValueError("Invalid [toml] syntax in .gd-audit.toml")

    monkeypatch.setattr("gdaudit.cli.GodotProject", _raise_config_error)
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 2
    assert "Configuration error:" in result.output
    assert "[toml]" in result.output


def test_cli_invalid_baseline_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ValueError or OSError while loading a baseline file exits with code 2."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="BaseTest"\n', encoding="utf-8"
    )
    bad_baseline = tmp_path / "bad_baseline.json"
    bad_baseline.write_text("{not valid json", encoding="utf-8")

    def _raise_bad_baseline(*args: object, **kwargs: object) -> tuple[set[object], set[str]]:
        raise ValueError("Corrupt [baseline] JSON")

    monkeypatch.setattr("gdaudit.cli.load_baseline", _raise_bad_baseline)
    result = runner.invoke(app, ["scan", str(tmp_path), "--baseline", str(bad_baseline)])
    assert result.exit_code == 2
    assert "Baseline error:" in result.output
    assert "[baseline]" in result.output


def test_cli_github_output_creates_parent_dirs(tmp_path: Path) -> None:
    """--format github --output creates missing parent directories."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="GHOut"\n', encoding="utf-8"
    )
    out_file = tmp_path / "nested" / "dir" / "annotations.txt"

    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--format", "github", "--output", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.exists()


def test_cli_root_help_epilog() -> None:
    """Root help explains that gd-audit <path> delegates to scan."""
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    normalized = " ".join(result.output.split())
    assert "scan" in normalized
    assert "gd-audit scan -h" in normalized


def test_cli_step_summary_single_pass(tmp_path: Path) -> None:
    """--step-summary writes Markdown summary alongside primary JSON output."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="SummaryProj"\n', encoding="utf-8"
    )
    summary_file = tmp_path / "ci" / "summary.md"

    result = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--format",
            "json",
            "--step-summary",
            str(summary_file),
        ],
    )
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["project"]["name"] == "SummaryProj"
    assert summary_file.exists()
    md_content = summary_file.read_text(encoding="utf-8")
    assert "SummaryProj" in md_content


def test_cli_quiet_suppresses_rich_output(tmp_path: Path) -> None:
    """--quiet suppresses non-essential rich console output."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="QuietProj"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_cli_update_baseline_requires_baseline(tmp_path: Path) -> None:
    """--update-baseline without --baseline exits with code 2."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="BaseProj"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["scan", str(tmp_path), "--update-baseline"])
    assert result.exit_code == 2


def test_cli_update_baseline_overwrites_existing(tmp_path: Path) -> None:
    """--update-baseline overwrites an existing baseline file with current issues."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="BaseProj"\n', encoding="utf-8"
    )
    (tmp_path / "main.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="Script" path="res://missing.gd" id="1"]\n',
        encoding="utf-8",
    )
    baseline_file = tmp_path / ".gd-audit-baseline.json"
    baseline_file.write_text('{"version": 2, "issues": []}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--baseline",
            str(baseline_file),
            "--update-baseline",
            "--quiet",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert len(data["issues"]) >= 1


def test_cli_changed_only_outside_git_fails_cleanly(tmp_path: Path) -> None:
    """--changed-only outside a Git repo exits with code 2 and clear message."""
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="NoGit"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["scan", str(tmp_path), "--changed-only"])
    assert result.exit_code == 2
    assert "Git" in result.output or "git" in result.output.lower()


def _init_git_repo(repo: Path) -> None:
    """Initialize a minimal Git repo with local user config."""
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_cli_changed_only_filters_issues_and_exit_code(tmp_path: Path) -> None:
    """--changed-only filters reported issues and exit code to modified files."""
    _init_git_repo(tmp_path)
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="GitProj"\n', encoding="utf-8"
    )
    (tmp_path / "old_broken.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="Script" path="res://missing_old.gd" id="1"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )

    # Create a clean untracked file -> old_broken.tscn is unmodified, so 0 issues & exit 0
    (tmp_path / "clean.gd").write_text("extends Node\n", encoding="utf-8")
    res_clean = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--changed-only",
            "--no-check-orphans",
            "--format",
            "json",
            "--fail-on",
            "high",
        ],
    )
    assert res_clean.exit_code == 0
    assert json.loads(res_clean.output)["issues"] == []

    # Now add a broken untracked scene -> reported & fails with exit 1
    (tmp_path / "new_broken.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="Script" path="res://missing_new.gd" id="1"]\n',
        encoding="utf-8",
    )
    res_broken = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--changed-only",
            "--no-check-orphans",
            "--format",
            "json",
            "--fail-on",
            "high",
        ],
    )
    assert res_broken.exit_code == 1
    issues = json.loads(res_broken.output)["issues"]
    assert len(issues) >= 1
    assert all(i["file_path"] == "new_broken.tscn" for i in issues)


def test_cli_diff_ref_filters_and_rejects_bad_ref(tmp_path: Path) -> None:
    """--diff <ref> filters to files changed since ref and fails with code 2 on bad ref."""
    _init_git_repo(tmp_path)
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="DiffProj"\n', encoding="utf-8"
    )
    (tmp_path / "old_broken.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="Script" path="res://missing_old.gd" id="1"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "commit 1"], cwd=tmp_path, check=True, capture_output=True
    )

    (tmp_path / "new_broken.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="Script" path="res://missing_new.gd" id="1"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "commit 2"], cwd=tmp_path, check=True, capture_output=True
    )

    res_diff = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--diff",
            "HEAD~1",
            "--format",
            "json",
            "--fail-on",
            "never",
        ],
    )
    assert res_diff.exit_code == 0
    issues = json.loads(res_diff.output)["issues"]
    assert len(issues) >= 1
    assert all(i["file_path"] == "new_broken.tscn" for i in issues)

    res_bad = runner.invoke(
        app,
        ["scan", str(tmp_path), "--diff", "nonexistent-ref-xyz"],
    )
    assert res_bad.exit_code == 2
