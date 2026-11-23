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


def test_find_project_root_nonexistent_path_does_not_climb_to_parent(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    nonexistent_child = tmp_path / "does_not_exist" / "nested"

    with pytest.raises(FileNotFoundError, match="Path does not exist"):
        find_project_root(nonexistent_child)


def test_find_project_root_deterministic_child_order(tmp_path: Path) -> None:
    for name in ("z_game", "a_game", "m_game"):
        sub = tmp_path / name
        sub.mkdir()
        (sub / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    root = find_project_root(tmp_path)
    assert root == (tmp_path / "a_game").resolve()


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
    assert data["autoloads"]["SoundManager"].is_singleton is False
    assert data["autoloads"]["SoundManager"].path == "res://autoloads/sound.tscn"


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


def test_res_literal_regex_supports_quoted_paths_with_spaces(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "Main Theme.ogg").write_bytes(b"ogg")
    (assets_dir / "My Icon.png").write_bytes(b"png")
    (tmp_path / "loader.gd").write_text(
        "extends Node\n"
        'const BGM = "res://assets/Main Theme.ogg"\n'
        "const ICON = 'res://assets/My Icon.png'\n",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    assert project.is_res_referenced("res://assets/Main Theme.ogg")
    assert project.is_res_referenced("res://assets/My Icon.png")


def test_dynamic_res_prefix_and_normalize_edge_cases() -> None:
    from gdaudit.utils.godot_project import dynamic_res_prefix

    assert dynamic_res_prefix("assets/icons/") is None
    assert dynamic_res_prefix("res://assets/../icons/") is None
    assert dynamic_res_prefix("res://assets/./icons/") is None
    assert dynamic_res_prefix("res://single/") is None
    assert dynamic_res_prefix("res://a/b/") == "res://a/b/"
    assert dynamic_res_prefix("res://a/item_%s.tres") == "res://a/item_"
    assert dynamic_res_prefix("res://plain.gd") is None

    assert normalize_res_path("plain/path.gd") == "plain/path.gd"
    assert normalize_res_path("res://../outside.gd") == "res://../outside.gd"


def test_find_project_root_with_project_godot_file_and_path_to_res_outside(tmp_path: Path) -> None:
    from gdaudit.utils.godot_project import (
        find_project_root,
        is_binary_file,
        path_to_res,
    )

    proj_dir = tmp_path / "game"
    proj_dir.mkdir()
    proj_file = proj_dir / "project.godot"
    proj_file.write_text("config_version=5\n", encoding="utf-8")

    assert find_project_root(proj_file) == proj_dir.resolve()

    outside = tmp_path / "other" / "file.gd"
    outside.parent.mkdir()
    outside.write_text("extends Node\n", encoding="utf-8")
    assert path_to_res(outside, proj_dir).startswith("res://")

    assert is_binary_file(tmp_path / "does_not_exist.bin") is False

    import pytest

    with pytest.raises(FileNotFoundError):
        parse_project_godot(tmp_path / "missing_project.godot")


def test_is_ignored_config_icon_entry_scenes_and_exists_on_disk_traversal(tmp_path: Path) -> None:
    from gdaudit.config import AuditConfig

    (tmp_path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/icon="res://icon.svg"\n',
        encoding="utf-8",
    )
    (tmp_path / "icon.svg").write_text("<svg/>\n", encoding="utf-8")
    (tmp_path / "entry1.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (tmp_path / "entry2.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    gdignore_dir = tmp_path / "ignored_sub"
    gdignore_dir.mkdir()
    (gdignore_dir / ".gdignore").write_text("", encoding="utf-8")
    (gdignore_dir / "skip.gd").write_text("extends Node\n", encoding="utf-8")

    addons_dir = tmp_path / "addons" / "plugin"
    addons_dir.mkdir(parents=True)
    (addons_dir / "tool.gd").write_text("extends Node\n", encoding="utf-8")

    gen_dir = tmp_path / "generated"
    gen_dir.mkdir()
    (gen_dir / "out.gd").write_text("extends Node\n", encoding="utf-8")

    cfg = AuditConfig(
        ignore=["  ", "generated"],
        entry_scenes=["", "entry1.tscn", "res://entry2.tscn"],
    )
    project = GodotProject(tmp_path, config=cfg)

    assert project.is_ignored(tmp_path / ".git" / "config", is_dir=False) is True
    assert project.is_ignored(addons_dir / "tool.gd", is_dir=False) is True
    assert project.is_ignored(gdignore_dir / "skip.gd", is_dir=False) is True
    assert project.is_ignored(gen_dir / "out.gd", is_dir=False) is True

    assert "res://icon.svg" in project.referenced_res
    assert "res://entry1.tscn" in project.referenced_res
    assert "res://entry2.tscn" in project.referenced_res
    assert project.exists_on_disk("res://../../outside_secret.txt") is False


def test_external_symlink_rejected_in_is_ignored_and_index(tmp_path: Path, monkeypatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    secret = tmp_path / "outside_secret.gd"
    secret.write_text("extends Node\nvar secret_token = 'abc'\n", encoding="utf-8")
    link_file = proj / "linked.gd"
    link_file.write_text("extends Node\n", encoding="utf-8")

    orig_is_symlink = Path.is_symlink
    orig_resolve = Path.resolve

    def fake_is_symlink(self: Path) -> bool:
        if self.name == "linked.gd":
            return True
        return orig_is_symlink(self)

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if self.name == "linked.gd":
            return orig_resolve(secret, strict=strict)
        return orig_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "resolve", fake_resolve)

    project = GodotProject(proj)
    assert project.is_ignored(link_file, is_dir=False) is True
    assert link_file not in project.gd_files
    assert link_file not in project.file_texts


def test_max_text_file_bytes_skips_oversized_files(tmp_path: Path, monkeypatch) -> None:
    import gdaudit.utils.godot_project as gp_mod

    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    small = tmp_path / "small.gd"
    small.write_text("extends Node\n", encoding="utf-8")
    huge = tmp_path / "huge.tres"
    huge.write_text("[gd_resource format=3]\n" + ("A" * 512), encoding="utf-8")

    monkeypatch.setattr(gp_mod, "MAX_TEXT_FILE_BYTES", 128)

    project = GodotProject(tmp_path)
    assert small in project.file_texts
    assert huge not in project.file_texts


def test_uid_sidecar_and_header_indexing(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "player.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "player.gd.uid").write_text("uid://player123\n", encoding="utf-8")
    (tmp_path / "enemy.tscn").write_text(
        '[gd_scene format=3 uid="uid://enemy456"]\n[node name="Enemy" type="Node2D"]\n',
        encoding="utf-8",
    )
    (tmp_path / "main.tscn").write_text(
        "[gd_scene format=3]\n"
        '[ext_resource type="Script" uid="uid://player123" id="1"]\n'
        '[node name="Main" type="Node"]\n'
        'script = ExtResource("1")\n',
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    assert project.resolve_uid("uid://player123") == "res://player.gd"
    assert project.resolve_uid("uid://enemy456") == "res://enemy.tscn"
    assert project.is_res_referenced("res://player.gd") is True
