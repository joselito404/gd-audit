"""Unit tests for the Typer CLI application."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gdaudit.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


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
