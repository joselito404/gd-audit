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
