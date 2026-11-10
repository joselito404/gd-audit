"""Unit tests for Godot project discovery and configuration parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdaudit.utils.godot_project import (
    GodotProject,
    find_project_root,
    parse_project_godot,
    path_to_res,
    res_to_path,
)


def test_find_project_root_direct(tmp_path: Path) -> None:
    project_godot = tmp_path / "project.godot"
    project_godot.write_text("config_version=5\n", encoding="utf-8")

    root = find_project_root(tmp_path)
    assert root == tmp_path


def test_find_project_root_from_child(tmp_path: Path) -> None:
    project_godot = tmp_path / "project.godot"
    project_godot.write_text("config_version=5\n", encoding="utf-8")

    child_dir = tmp_path / "scenes" / "levels"
    child_dir.mkdir(parents=True)

    root = find_project_root(child_dir)
    assert root == tmp_path


def test_find_project_root_from_file(tmp_path: Path) -> None:
    project_godot = tmp_path / "project.godot"
    project_godot.write_text("config_version=5\n", encoding="utf-8")

    script = tmp_path / "main.gd"
    script.write_text("extends Node\n", encoding="utf-8")

    root = find_project_root(script)
    assert root == tmp_path


def test_find_project_root_not_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        find_project_root(empty_dir)


def test_res_to_path_and_path_to_res(tmp_path: Path) -> None:
    scene_file = tmp_path / "scenes" / "player.tscn"
    scene_file.parent.mkdir(parents=True)
    scene_file.write_text("[gd_scene format=3]\n", encoding="utf-8")

    res_uri = path_to_res(scene_file, tmp_path)
    assert res_uri == "res://scenes/player.tscn"

    resolved = res_to_path(res_uri, tmp_path)
    assert resolved == scene_file.resolve()


def test_parse_project_godot(tmp_path: Path) -> None:
    cfg = tmp_path / "project.godot"
    cfg.write_text(
        """
config_version=5

[application]
config/name="Space Shooter"
run/main_scene="res://main.tscn"
config/features=PackedStringArray("4.3", "Forward Plus")

[autoload]
GameState="*res://autoloads/game_state.gd"
SoundManager="res://autoloads/sound.tscn"
""",
        encoding="utf-8",
    )

    data = parse_project_godot(cfg)
    assert data["name"] == "Space Shooter"
    assert data["main_scene"] == "res://main.tscn"
    assert "GameState" in data["autoloads"]
    assert data["autoloads"]["GameState"].is_singleton is True
    assert data["autoloads"]["GameState"].path == "res://autoloads/game_state.gd"
    assert "SoundManager" in data["autoloads"]


def test_godot_project_discovery(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "main.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (tmp_path / "theme.tres").write_text("[gd_resource format=3]\n", encoding="utf-8")
    (tmp_path / "icon.svg").write_text("<svg></svg>\n", encoding="utf-8")

    # Ignored directory
    ignored_dir = tmp_path / ".godot" / "imported"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "test.gd").write_text("should be ignored", encoding="utf-8")

    project = GodotProject(tmp_path, ignore_patterns=["*.tmp"])
    stats = project.stats

    assert stats["gd_scripts"] == 1
    assert stats["scenes"] == 1
    assert stats["resources"] == 1
    assert stats["assets"] == 1
