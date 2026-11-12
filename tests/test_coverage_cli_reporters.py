"""Comprehensive coverage and robustness tests for CLI, reporters, and irregular files.

Targets:
- src/gdaudit/cli.py >= 90% coverage
- Complex project paths (spaces, nonexistent, permission errors)
- Complex encodings and irregular files (cp1252, latin-1, utf-8-sig BOM, etc.)
- CLI reporters and output formats (json, markdown, github, rich, stdout vs file)
- Combined options (--ignore-paths, --no-check-orphans, --include-addons, --fail-on)
"""

from __future__ import annotations

import json
import runpy
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from gdaudit import __version__
from gdaudit.cli import app, configure_utf8_output, version_callback
from gdaudit.models import AuditResult, Severity
from gdaudit.reporters.console import render_console
from gdaudit.reporters.markdown import render_markdown

runner = CliRunner()


def _create_clean_project(root: Path, name: str = "CleanProject") -> Path:
    """Create a minimal valid Godot 4 project."""
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
    """Create a Godot 4 project containing a broken resource (HIGH severity issue)."""
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
# 1. AuditGroup and CLI Command Routing
# ==============================================================================


def test_audit_group_empty_args_routes_to_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invoking app with no arguments routes to 'scan' using cwd."""
    project_dir = _create_clean_project(tmp_path / "CwdProject")
    monkeypatch.chdir(project_dir)

    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_audit_group_empty_args_in_non_godot_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invoking app with no arguments in a non-Godot folder exits with code 1."""
    empty_dir = tmp_path / "empty_folder"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)

    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Could not locate 'project.godot'" in result.output


def test_audit_group_direct_path_without_subcommand(tmp_path: Path) -> None:
    """Passing project path directly without 'scan' subcommand routes automatically to scan."""
    project_dir = _create_clean_project(tmp_path / "DirectPathProject")

    result = runner.invoke(app, [str(project_dir)])
    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_audit_group_flags_without_subcommand(tmp_path: Path) -> None:
    """Passing CLI options directly without 'scan' subcommand routes automatically to scan."""
    project_dir = _create_clean_project(tmp_path / "FlagsProject")

    result = runner.invoke(app, ["--format", "json", str(project_dir)])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["project"]["name"] == "CleanProject"


def test_audit_group_explicit_scan_subcommand(tmp_path: Path) -> None:
    """Explicitly invoking the 'scan' subcommand works as expected."""
    project_dir = _create_clean_project(tmp_path / "ExplicitScanProject")

    result = runner.invoke(app, ["scan", str(project_dir)])
    assert result.exit_code == 0
    assert "All checks passed" in result.output


# ==============================================================================
# 2. Version Flags and Version Subcommand
# ==============================================================================


def test_cli_version_flag_long() -> None:
    """Flag --version prints version and exits with code 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"gd-audit v{__version__}" in result.output


def test_cli_version_flag_short() -> None:
    """Flag -v prints version and exits with code 0."""
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert f"gd-audit v{__version__}" in result.output


def test_cli_version_command() -> None:
    """Subcommand 'version' prints version and exits with code 0."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"gd-audit v{__version__}" in result.output


def test_version_callback_noop() -> None:
    """Calling version_callback(False) does nothing without raising."""
    version_callback(False)


# ==============================================================================
# 3. UTF-8 Output Configuration Robustness
# ==============================================================================


def test_configure_utf8_output_handles_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streams raising ValueError on reconfigure (e.g., closed stream) are handled silently."""

    class FailingStream:
        def reconfigure(self, **kwargs: Any) -> None:
            raise ValueError("simulated closed stream")

    monkeypatch.setattr(sys, "stdout", FailingStream())
    configure_utf8_output()


def test_configure_utf8_output_handles_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streams raising OSError on reconfigure are handled silently."""

    class FailingStream:
        def reconfigure(self, **kwargs: Any) -> None:
            raise OSError("simulated bad file descriptor")

    monkeypatch.setattr(sys, "stderr", FailingStream())
    configure_utf8_output()


def test_configure_utf8_output_handles_missing_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streams without reconfigure method are skipped cleanly."""
    monkeypatch.setattr(sys, "stdout", object())
    configure_utf8_output()


# ==============================================================================
# 4. Output Formats (Stdout and Output Files)
# ==============================================================================


def test_format_json_stdout_and_file(tmp_path: Path) -> None:
    """Verify --format json to stdout and to an output file."""
    project_dir = _create_clean_project(tmp_path / "JsonProj")

    # To stdout
    res_stdout = runner.invoke(app, ["scan", str(project_dir), "--format", "json"])
    assert res_stdout.exit_code == 0
    data = json.loads(res_stdout.output)
    assert data["project"]["name"] == "CleanProject"
    assert "summary" in data

    # To output file
    out_file = tmp_path / "report.json"
    res_file = runner.invoke(
        app, ["scan", str(project_dir), "--format", "json", "--output", str(out_file)]
    )
    assert res_file.exit_code == 0
    assert out_file.is_file()
    file_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert file_data["project"]["name"] == "CleanProject"


def test_format_markdown_stdout_and_file(tmp_path: Path) -> None:
    """Verify --format markdown to stdout and to an output file."""
    project_dir = _create_clean_project(tmp_path / "MdProj")

    # To stdout
    res_stdout = runner.invoke(app, ["scan", str(project_dir), "--format", "markdown"])
    assert res_stdout.exit_code == 0
    assert "# 🛡️ gd-audit Report" in res_stdout.output
    assert "**Project:** `CleanProject`" in res_stdout.output

    # To output file (should not echo to stdout)
    out_file = tmp_path / "report.md"
    res_file = runner.invoke(
        app, ["scan", str(project_dir), "--format", "markdown", "--output", str(out_file)]
    )
    assert res_file.exit_code == 0
    assert out_file.is_file()
    md_content = out_file.read_text(encoding="utf-8")
    assert "# 🛡️ gd-audit Report" in md_content


def test_format_github_clean_project(tmp_path: Path) -> None:
    """Verify --format github with no issues outputs nothing to stdout."""
    project_dir = _create_clean_project(tmp_path / "GhCleanProj")

    res = runner.invoke(app, ["scan", str(project_dir), "--format", "github"])
    assert res.exit_code == 0
    assert res.output.strip() == ""


def test_format_github_with_issues_stdout_and_file(tmp_path: Path) -> None:
    """Verify --format github outputs annotations to stdout and to an output file."""
    broken_dir = _create_broken_project(tmp_path / "GhBrokenProj")

    # To stdout
    res_stdout = runner.invoke(
        app, ["scan", str(broken_dir), "--format", "github", "--fail-on", "never"]
    )
    assert res_stdout.exit_code == 0
    assert "::error file=" in res_stdout.output
    assert "missing_script.gd" in res_stdout.output

    # To output file
    out_file = tmp_path / "gh_annotations.txt"
    res_file = runner.invoke(
        app,
        [
            "scan",
            str(broken_dir),
            "--format",
            "github",
            "--output",
            str(out_file),
            "--fail-on",
            "never",
        ],
    )
    assert res_file.exit_code == 0
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert "::error file=" in content


def test_format_rich_with_output_file(tmp_path: Path) -> None:
    """Verify --format rich writes markdown to --output when specified."""
    project_dir = _create_clean_project(tmp_path / "RichProj")
    out_file = tmp_path / "rich_summary.md"

    res = runner.invoke(
        app, ["scan", str(project_dir), "--format", "rich", "--output", str(out_file)]
    )
    assert res.exit_code == 0
    assert out_file.is_file()
    assert "# 🛡️ gd-audit Report" in out_file.read_text(encoding="utf-8")


def test_format_invalid_option(tmp_path: Path) -> None:
    """Passing an unsupported format exits with code 2."""
    project_dir = _create_clean_project(tmp_path / "InvalidFmtProj")

    res = runner.invoke(app, ["scan", str(project_dir), "--format", "unsupported_fmt"])
    assert res.exit_code == 2
    assert "Invalid format:" in res.output
    assert "unsupported_fmt" in res.output


# ==============================================================================
# 5. Severity Filtering and Invalid Severity
# ==============================================================================


def test_cli_invalid_severity(tmp_path: Path) -> None:
    """Passing an invalid severity string exits with code 2 and informative message."""
    project_dir = _create_clean_project(tmp_path / "InvalidSevProj")

    res = runner.invoke(app, ["scan", str(project_dir), "--severity", "extreme"])
    assert res.exit_code == 2
    assert "Invalid severity:" in res.output
    assert "extreme" in res.output


def test_cli_invalid_fail_on(tmp_path: Path) -> None:
    """Passing an invalid fail-on string exits with code 2 and informative message."""
    project_dir = _create_clean_project(tmp_path / "InvalidFailOnProj")

    res = runner.invoke(app, ["scan", str(project_dir), "--fail-on", "invalid_threshold"])
    assert res.exit_code == 2
    assert "Invalid --fail-on:" in res.output
    assert "invalid_threshold" in res.output


def test_cli_severity_filter_applied(tmp_path: Path) -> None:
    """--severity filters displayed issues according to threshold."""
    broken_dir = _create_broken_project(tmp_path / "FilterSevProj")

    # Broken script reference is HIGH severity. If minimum severity is CRITICAL, it won't display.
    res_crit = runner.invoke(
        app,
        [
            "scan",
            str(broken_dir),
            "--severity",
            "critical",
            "--format",
            "json",
            "--fail-on",
            "never",
        ],
    )
    assert res_crit.exit_code == 0
    data_crit = json.loads(res_crit.output)
    assert len(data_crit["issues"]) == 0

    # With minimum severity HIGH, it is included
    res_high = runner.invoke(
        app,
        ["scan", str(broken_dir), "--severity", "high", "--format", "json", "--fail-on", "never"],
    )
    assert res_high.exit_code == 0
    data_high = json.loads(res_high.output)
    assert len(data_high["issues"]) >= 1


# ==============================================================================
# 6. Complex Project Paths & Error Handling
# ==============================================================================


def test_cli_project_path_with_spaces(tmp_path: Path) -> None:
    """Projects in directories with whitespace are scanned properly."""
    proj_dir = _create_clean_project(tmp_path / "Mi Proyecto Godot 4 Espacios")

    result = runner.invoke(app, ["scan", str(proj_dir)])
    assert result.exit_code == 0
    assert "CleanProject" in result.output or "0 high" in result.output


def test_cli_nonexistent_directory(tmp_path: Path) -> None:
    """Scanning a nonexistent directory exits with code 1 and Error."""
    nonexistent = tmp_path / "does_not_exist_folder"

    result = runner.invoke(app, ["scan", str(nonexistent)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Could not locate 'project.godot'" in result.output
    assert "does_not_exist_folder" in result.output.replace("\n", "")


def test_cli_directory_without_project_godot(tmp_path: Path) -> None:
    """Scanning an existing directory without project.godot exits with code 1."""
    empty_dir = tmp_path / "existing_folder_no_godot"
    empty_dir.mkdir()

    result = runner.invoke(app, ["scan", str(empty_dir)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Could not locate 'project.godot'" in result.output


def test_cli_unexpected_error_discovering_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unexpected exceptions during project discovery are cleanly handled with exit 1."""
    proj_dir = _create_clean_project(tmp_path / "CrashProj")

    def _mock_raise(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("Access denied: simulated lack of disk permissions")

    monkeypatch.setattr("gdaudit.cli.GodotProject", _mock_raise)

    result = runner.invoke(app, ["scan", str(proj_dir)])
    assert result.exit_code == 1
    assert "Unexpected error discovering project:" in result.output
    assert "Access denied" in result.output


# ==============================================================================
# 7. Complex Encodings and Irregular Files
# ==============================================================================


def test_project_with_cp1252_latin1_script(tmp_path: Path) -> None:
    """Scripts with non-UTF8 / cp1252 / latin-1 characters are safely ingested."""
    proj_dir = _create_clean_project(tmp_path / "Latin1Proj")

    script_path = proj_dir / "cp1252_script.gd"
    # Contains accented Latin-1 bytes (é = 0xe9, ñ = 0xf1)
    script_path.write_bytes(
        b"# Autor: Jos\xe9 Pe\xf1a\nextends Node\nfunc calcular_da\xf1o():\n\tpass\n"
    )

    # Reference it in main.tscn to avoid orphan detection
    (proj_dir / "main.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://cp1252_script.gd" id="1_cp"]
[node name="Node" type="Node"]
script = ExtResource("1_cp")
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(proj_dir)])
    assert result.exit_code == 0
    assert "UnicodeDecodeError" not in result.output


def test_project_with_utf8_sig_bom_files(tmp_path: Path) -> None:
    """Godot files with UTF-8 Byte Order Mark (BOM) are parsed without errors."""
    proj_dir = tmp_path / "BomProj"
    proj_dir.mkdir()

    # project.godot with BOM
    bom = b"\xef\xbb\xbf"
    (proj_dir / "project.godot").write_bytes(
        bom
        + b"""config_version=5
[application]
config/name="BomGame"
run/main_scene="res://main.tscn"
"""
    )

    # main.tscn with BOM
    (proj_dir / "main.tscn").write_bytes(
        bom
        + b"""[gd_scene format=3]
[ext_resource type="Script" path="res://bom_script.gd" id="1_bom"]
[node name="Node" type="Node"]
script = ExtResource("1_bom")
"""
    )

    # script with BOM
    (proj_dir / "bom_script.gd").write_bytes(bom + b"extends Node\nfunc _ready():\n\tpass\n")

    result = runner.invoke(app, ["scan", str(proj_dir)])
    assert result.exit_code == 0
    assert "BomGame" in result.output or "0 high" in result.output


def test_project_with_massive_empty_lines(tmp_path: Path) -> None:
    """Scripts with massive empty line gaps are analyzed quickly without issue."""
    proj_dir = _create_clean_project(tmp_path / "MassiveLinesProj")

    huge_script = (
        "extends Node\nfunc first():\n\tpass\n" + ("\n" * 8000) + "func second():\n\tpass\n"
    )
    (proj_dir / "massive.gd").write_text(huge_script, encoding="utf-8")

    (proj_dir / "main.tscn").write_text(
        """[gd_scene format=3]
[ext_resource type="Script" path="res://massive.gd" id="1_m"]
[node name="Node" type="Node"]
script = ExtResource("1_m")
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(proj_dir)])
    assert result.exit_code == 0


def test_project_with_binary_or_null_bytes_in_gd_file(tmp_path: Path) -> None:
    """Corrupted or binary files with .gd extension are detected by null bytes and skipped."""
    proj_dir = _create_clean_project(tmp_path / "CorruptFileProj")

    # File with null bytes
    (proj_dir / "corrupted.gd").write_bytes(b"extends Node\x00\x00corrupt_data\x00")

    result = runner.invoke(app, ["scan", str(proj_dir), "--no-check-orphans"])
    assert result.exit_code == 0


# ==============================================================================
# 8. Combined Options (--ignore-paths, --check-orphans, --include-addons, --fail-on)
# ==============================================================================


def test_cli_ignore_paths_with_commas_and_spaces(tmp_path: Path) -> None:
    """--ignore-paths parses comma and space-delimited patterns properly."""
    proj_dir = _create_clean_project(tmp_path / "IgnorePathsProj")

    # Add extra files in ignore targets
    scratch_dir = proj_dir / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "dummy.png").write_bytes(b"image")

    addons_custom = proj_dir / "custom_dir"
    addons_custom.mkdir()
    (addons_custom / "unreferenced.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    # Without ignore paths, orphans would be detected
    res_no_ignore = runner.invoke(app, ["scan", str(proj_dir), "--format", "json"])
    assert res_no_ignore.exit_code == 0
    data_no_ignore = json.loads(res_no_ignore.output)
    assert len(data_no_ignore["issues"]) >= 2

    # With combined ignore-paths patterns
    res_ignored = runner.invoke(
        app,
        [
            "scan",
            str(proj_dir),
            "--format",
            "json",
            "--ignore-paths",
            "scratch/**, custom_dir/* tests/**",
        ],
    )
    assert res_ignored.exit_code == 0
    data_ignored = json.loads(res_ignored.output)
    assert len(data_ignored["issues"]) == 0


def test_cli_check_orphans_toggle(tmp_path: Path) -> None:
    """--no-check-orphans disables detection of unreferenced assets."""
    proj_dir = _create_clean_project(tmp_path / "OrphansToggleProj")
    (proj_dir / "lonely_asset.png").write_bytes(b"png data")

    # By default, orphan is flagged
    res_default = runner.invoke(app, ["scan", str(proj_dir), "--format", "json"])
    assert res_default.exit_code == 0
    assert len(json.loads(res_default.output)["issues"]) == 1

    # With --no-check-orphans, it is not flagged
    res_disabled = runner.invoke(
        app, ["scan", str(proj_dir), "--format", "json", "--no-check-orphans"]
    )
    assert res_disabled.exit_code == 0
    assert len(json.loads(res_disabled.output)["issues"]) == 0


def test_cli_include_addons_toggle(tmp_path: Path) -> None:
    """--include-addons controls whether the root addons/ folder is scanned."""
    proj_dir = _create_clean_project(tmp_path / "AddonsProj")
    addon_dir = proj_dir / "addons" / "my_plugin"
    addon_dir.mkdir(parents=True)
    (addon_dir / "plugin_orphan.png").write_bytes(b"plugin icon")

    # By default, root addons/ is ignored
    res_default = runner.invoke(app, ["scan", str(proj_dir), "--format", "json"])
    assert res_default.exit_code == 0
    assert len(json.loads(res_default.output)["issues"]) == 0

    # With --include-addons, addons/ is scanned and orphan is found
    res_addons = runner.invoke(app, ["scan", str(proj_dir), "--format", "json", "--include-addons"])
    assert res_addons.exit_code == 0
    assert len(json.loads(res_addons.output)["issues"]) == 1


@pytest.mark.parametrize(
    ("fail_on_level", "expected_code"),
    [
        ("critical", 0),  # Project has HIGH issue, not critical -> passes
        ("high", 1),  # Project has HIGH issue -> fails
        ("medium", 1),  # HIGH >= medium -> fails
        ("low", 1),  # HIGH >= low -> fails
        ("never", 0),  # fail-on never -> always passes
    ],
)
def test_cli_fail_on_thresholds(tmp_path: Path, fail_on_level: str, expected_code: int) -> None:
    """Verify exit codes (0 vs 1) for various --fail-on thresholds against a HIGH issue."""
    broken_dir = _create_broken_project(tmp_path / f"FailOn_{fail_on_level}")

    result = runner.invoke(app, ["scan", str(broken_dir), "--fail-on", fail_on_level])
    assert result.exit_code == expected_code


def test_cli_baseline_record_and_filter(tmp_path: Path) -> None:
    """--baseline records issues when file is missing and suppresses them on subsequent scans."""
    broken_dir = _create_broken_project(tmp_path / "BaselineProj")
    baseline_path = tmp_path / "audit_baseline.json"

    # 1. First run: baseline does not exist -> creates baseline and resets issues
    res_record = runner.invoke(
        app, ["scan", str(broken_dir), "--baseline", str(baseline_path), "--fail-on", "high"]
    )
    assert res_record.exit_code == 0
    assert baseline_path.is_file()
    assert "Baseline generated at" in res_record.output

    # 2. Second run: baseline exists -> loaded and suppresses recorded issues
    res_filter = runner.invoke(
        app, ["scan", str(broken_dir), "--baseline", str(baseline_path), "--fail-on", "high"]
    )
    assert res_filter.exit_code == 0
    assert "All checks passed" in res_filter.output


# ==============================================================================
# 9. Main Module Execution (__main__)
# ==============================================================================


def test_cli_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executing gdaudit.cli as __main__ invokes app() cleanly."""
    monkeypatch.setattr(sys, "argv", ["gd-audit", "version"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("gdaudit.cli", run_name="__main__")
        assert exc_info.value.code == 0


# ==============================================================================
# 10. Reporters Robustness & Severity Edge Cases
# ==============================================================================


def test_reporters_when_project_is_cwd() -> None:
    """Console, Markdown, and Result reporters handle project_path equal to cwd."""
    result = AuditResult(
        project_path=Path.cwd(),
        project_name="CurrentDirProject",
        issues=[],
    )
    console = Console(record=True, width=120)
    render_console(result, console=console)
    output = console.export_text()
    assert "CurrentDirProject" in output

    md_output = render_markdown(result)
    assert "**Project:** `CurrentDirProject`" in md_output

    data = result.to_dict()
    assert data["project"]["path"] == "CurrentDirProject"


def test_severity_rich_comparisons_and_not_implemented() -> None:
    """Severity comparisons handle Severity instances, strings, and unsupported types."""
    assert Severity.HIGH >= "medium"
    assert Severity.HIGH > "medium"
    assert Severity.LOW <= "medium"
    assert Severity.LOW < "medium"

    # Unsupported comparison returns NotImplemented (evaluating as False or TypeError)
    other_val: Any = 12345
    assert not (Severity.HIGH == other_val)
    with pytest.raises(TypeError):
        _ = Severity.HIGH >= other_val
    with pytest.raises(TypeError):
        _ = Severity.HIGH > other_val
    with pytest.raises(TypeError):
        _ = Severity.LOW <= other_val
    with pytest.raises(TypeError):
        _ = Severity.LOW < other_val
