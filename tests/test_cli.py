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

from gdaudit.cli import app, configure_utf8_output

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
