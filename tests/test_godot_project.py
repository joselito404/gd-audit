"""Unit tests for Godot project discovery and configuration parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdaudit.utils.godot_project import (
    GodotProject,
    find_project_root,
    is_binary_file,
    normalize_res_path,
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


def test_res_to_path_security_traversal(tmp_path: Path) -> None:
    # 1. Path traversal attempting to escape project root
    with pytest.raises(ValueError, match="Path traversal detected"):
        res_to_path("res://../../etc/passwd", tmp_path)

    with pytest.raises(ValueError, match="Path traversal detected"):
        res_to_path("res://../outside.gd", tmp_path)

    # 2. UNC path rejection
    with pytest.raises(ValueError, match="UNC paths are not permitted"):
        res_to_path("//server/share/payload.gd", tmp_path)

    with pytest.raises(ValueError, match="UNC paths are not permitted"):
        res_to_path("\\\\server\\share\\payload.gd", tmp_path)

    with pytest.raises(ValueError, match="UNC paths are not permitted"):
        res_to_path("res:////network/share/test.gd", tmp_path)

    # 3. Leading slashes stripped to prevent root discard
    sub_file = tmp_path / "sub" / "file.gd"
    sub_file.parent.mkdir(parents=True)
    sub_file.write_text("extends Node", encoding="utf-8")

    resolved_leading = res_to_path("res:///sub/file.gd", tmp_path)
    assert resolved_leading == sub_file.resolve()

    # 4. Drive letter stripped safely
    resolved_drive = res_to_path("res://C:/sub/file.gd", tmp_path)
    assert resolved_drive == sub_file.resolve()


def test_is_binary_file(tmp_path: Path) -> None:
    text_file = tmp_path / "script.gd"
    text_file.write_text("extends Node\nfunc _ready(): pass", encoding="utf-8")
    assert is_binary_file(text_file) is False

    bin_file = tmp_path / "corrupted.gd"
    bin_file.write_bytes(b"extends Node\x00\x01\x02corrupted binary data")
    assert is_binary_file(bin_file) is True


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
        encoding="utf-8-sig",
    )

    data = parse_project_godot(cfg)
    assert data["name"] == "Space Shooter"
    assert data["main_scene"] == "res://main.tscn"
    assert "GameState" in data["autoloads"]
    assert data["autoloads"]["GameState"].is_singleton is True
    assert data["autoloads"]["GameState"].path == "res://autoloads/game_state.gd"
    assert "SoundManager" in data["autoloads"]


def test_parse_project_godot_with_bom(tmp_path: Path) -> None:
    cfg = tmp_path / "project.godot"
    # Write with explicit UTF-8 BOM
    cfg.write_bytes(b'\xef\xbb\xbfconfig_version=5\n[application]\nconfig/name="BOM Project"\n')

    data = parse_project_godot(cfg)
    assert data["name"] == "BOM Project"


def test_normalize_res_path() -> None:
    assert normalize_res_path("res://scenes/../scenes/player.tscn") == "res://scenes/player.tscn"
    assert normalize_res_path("res://./scripts/actor.gd") == "res://scripts/actor.gd"
    assert normalize_res_path("res://scripts\\actor.gd") == "res://scripts/actor.gd"


def test_godot_project_discovery_and_indexing(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        """config_version=5
[application]
run/main_scene="res://main.tscn"
[autoload]
Global="*res://global.gd"
""",
        encoding="utf-8-sig",
    )
    (tmp_path / "main.gd").write_text(
        "extends Node\nfunc _ready():\n    Global.ping()\n", encoding="utf-8"
    )
    (tmp_path / "global.gd").write_text("extends Node\nfunc ping(): pass\n", encoding="utf-8")
    (tmp_path / "main.tscn").write_text(
        """[gd_scene format=3]
[connection signal="pressed" from="Btn" to="." method="_on_button_pressed"]
""",
        encoding="utf-8",
    )
    (tmp_path / "theme.tres").write_text("[gd_resource format=3]\n", encoding="utf-8")
    (tmp_path / "icon.svg").write_text("<svg></svg>\n", encoding="utf-8")

    # Ignored directory
    ignored_dir = tmp_path / ".godot" / "imported"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "test.gd").write_text("should be ignored", encoding="utf-8")

    project = GodotProject(tmp_path, ignore_patterns=["*.tmp"])
    stats = project.stats

    assert stats["gd_scripts"] == 2
    assert stats["scenes"] == 1
    assert stats["resources"] == 1
    assert stats["assets"] == 1

    # Check pre-indexing caches
    assert "res://main.gd" in project.known_res
    assert "res://main.tscn" in project.referenced_res
    assert "res://global.gd" in project.referenced_res
    assert project.word_counts["Global"] >= 2
    assert "_on_button_pressed" in project.scene_connected_methods
