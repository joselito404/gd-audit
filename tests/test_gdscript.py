"""Unit tests for GDScript comment cleaning, hot loops, and dead function detection."""

from __future__ import annotations

from pathlib import Path

from gdaudit.core.gdscript import (
    GDScriptAnalyzer,
    clean_gdscript_comments,
    fast_clean_gdscript_comments,
    parse_functions,
)
from gdaudit.models import IssueCategory, Severity
from gdaudit.utils.godot_project import GodotProject


def test_clean_gdscript_comments() -> None:
    source = """# Header comment
var greeting = "Hello # World" # Inline comment
var multiline = \"\"\"
# inside triple double
\"\"\"
var single_comment = 'test # string' # trailing
"""
    cleaned = clean_gdscript_comments(source)
    assert "Header comment" not in cleaned
    assert '"Hello # World"' in cleaned
    assert "'test # string'" in cleaned
    assert "# inside triple double" in cleaned
    assert "trailing" not in cleaned

    # Verify fast_clean_gdscript_comments is identical
    assert fast_clean_gdscript_comments(source) == cleaned


def test_hot_loop_anti_patterns(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    script = tmp_path / "actor.gd"
    script.write_text(
        """extends CharacterBody2D

func _ready():
    # Outside hot loop: fine
    var node = get_node("Sprite")

func _process(delta):
    # Bad: get_node in _process
    var target = get_node("Target")
    var child = find_child("Weapon")
    var tex = load("res://texture.png")
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = GDScriptAnalyzer(project)
    issues = analyzer.analyze()

    hot_loop_issues = [i for i in issues if i.category == IssueCategory.PERFORMANCE_ANTIPATTERN]
    assert len(hot_loop_issues) >= 3
    assert all(i.severity == Severity.MEDIUM for i in hot_loop_issues)


def test_dead_function_candidate(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    script = tmp_path / "utils.gd"
    script.write_text(
        """extends Node

func _ready():
    # Built-in virtual method: should NOT be marked dead
    pass

func active_helper():
    pass

func completely_unused_func():
    pass
""",
        encoding="utf-8",
    )

    # active_helper is called in another file
    caller = tmp_path / "caller.gd"
    caller.write_text(
        """extends Node
func do_work():
    Utils.active_helper()
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = GDScriptAnalyzer(project)
    issues = analyzer.audit_dead_functions()

    dead_funcs = [i for i in issues if i.category == IssueCategory.DEAD_FUNCTION]
    names = [i.title for i in dead_funcs]

    assert any("completely_unused_func" in n for n in names)
    assert not any("active_helper" in n for n in names)
    assert not any("_ready" in n for n in names)


def test_dead_function_virtual_rpc_and_scene_signal(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    # Scene with a connection to on_button_pressed
    (tmp_path / "menu.tscn").write_text(
        """[gd_scene format=3]
[connection signal="pressed" from="Button" to="." method="on_button_pressed"]
""",
        encoding="utf-8",
    )

    script = tmp_path / "network_player.gd"
    script.write_text(
        """extends CharacterBody2D

static func _static_init():
    pass

func _integrate_forces(state):
    pass

func _get_minimum_size():
    return Vector2.ZERO

@rpc
func sync_coordinates():
    pass

@rpc("any_peer", "call_local")
func rpc_action():
    pass

func on_button_pressed():
    pass

func truly_dead():
    pass
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    analyzer = GDScriptAnalyzer(project)
    issues = analyzer.audit_dead_functions()

    dead_funcs = [i for i in issues if i.category == IssueCategory.DEAD_FUNCTION]
    names = [i.title for i in dead_funcs]

    # Only truly_dead should be flagged
    assert len(dead_funcs) == 1
    assert any("truly_dead" in n for n in names)
    assert not any("_static_init" in n for n in names)
    assert not any("_integrate_forces" in n for n in names)
    assert not any("_get_minimum_size" in n for n in names)
    assert not any("sync_coordinates" in n for n in names)
    assert not any("rpc_action" in n for n in names)
    assert not any("on_button_pressed" in n for n in names)


def test_parse_functions_multiline_sig_rpc_strings_and_static_whitespace() -> None:
    source = '''extends Node

@rpc(
    "any_peer",
    "call_local",
    "reliable",
)
func sync_multiline(
    pos: Vector2,
    vel: Vector2,
) -> void:
    var doc = """
column zero text inside triple quotes
func fake_inside_string():
    pass
"""
    var n = get_node("Target")

static   func   helper_static(x: int) -> int:
    return x + 1
'''
    cleaned = fast_clean_gdscript_comments(source)
    funcs = parse_functions(cleaned)
    assert [f.name for f in funcs] == ["sync_multiline", "helper_static"]

    sync_fn = funcs[0]
    assert sync_fn.is_rpc is True
    assert sync_fn.is_static is False
    assert any('get_node("Target")' in line for _, line in sync_fn.body_lines)
    assert not any("pos: Vector2" in line for _, line in sync_fn.body_lines)

    static_fn = funcs[1]
    assert static_fn.is_static is True
    assert static_fn.is_rpc is False


def test_godot4_virtual_and_logger_callbacks_not_flagged_dead(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "callbacks.gd").write_text(
        """extends Node

func _log_error(
    function, file, line, code, rationale, editor_notify, error_type, script_backtraces
):
    pass

func _log_message(message, error):
    pass

func _physics_interpolate(fraction):
    pass

func _get_aabb():
    return AABB()

func _get_item_rect():
    return Rect2()

func _process_custom_fx(char_fx):
    return true

func _import(source_file, save_path, options, platform_variants, gen_files):
    return OK

func _get_importer_name():
    return "custom.importer"

func _get_code(input_vars, output_vars, mode, type):
    return ""

func _get_name():
    return "CustomNode"
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = GDScriptAnalyzer(project).audit_dead_functions()
    assert issues == []
