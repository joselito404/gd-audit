"""Unit tests for GDScript comment cleaning, hot loops, and dead function detection."""

from __future__ import annotations

import time
from pathlib import Path

from gdaudit.core.gdscript import (
    GDScriptAnalyzer,
    clean_gdscript_comments,
    fast_clean_gdscript_comments,
    mask_gdscript_code,
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


def test_editor_import_plugin_virtuals_not_flagged_dead(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "custom_importer.gd").write_text(
        """extends EditorImportPlugin

func _get_importer_name():
    return "custom.importer"

func _get_recognized_extensions():
    return ["dat"]

func _get_save_extension():
    return "res"

func _get_resource_type():
    return "Resource"

func _get_preset_count():
    return 1

func _get_preset_name(preset_index):
    return "Default"

func _get_import_options(path, preset_index):
    return []

func _get_option_visibility(path, option_name, options):
    return true

func _import(source_file, save_path, options, platform_variants, gen_files):
    return OK
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = GDScriptAnalyzer(project).audit_dead_functions()
    assert issues == []


def test_raw_strings_comment_cleaning_and_masking() -> None:
    source = (
        'var a = r"hello # not a comment" # trailing comment\n'
        "var b = r'single # not a comment' # another comment\n"
        'var c = r"C:\\Users\\demo\\" # backslash before quote\n'
        'var d = r"""\n# multiline raw # comment\nget_node("X")\n"""\n'
    )
    cleaned = fast_clean_gdscript_comments(source)
    assert 'r"hello # not a comment"' in cleaned
    assert "r'single # not a comment'" in cleaned
    assert 'r"C:\\Users\\demo\\"' in cleaned
    assert "# multiline raw # comment" in cleaned
    assert "trailing comment" not in cleaned
    assert "another comment" not in cleaned
    assert "backslash before quote" not in cleaned

    masked = mask_gdscript_code(cleaned)
    assert len(masked) == len(cleaned)
    assert 'get_node("X")' not in masked
    assert "hello" not in masked
    assert "single" not in masked


def test_unclosed_triple_quotes_no_quadratic_redos() -> None:
    adversarial_double = 'var s = """\n' + ('"a" ' * 15000)
    adversarial_single = "var s = '''\n" + ("'a' " * 15000)

    t0 = time.perf_counter()
    cleaned_double = fast_clean_gdscript_comments(adversarial_double)
    masked_double = mask_gdscript_code(cleaned_double)
    cleaned_single = fast_clean_gdscript_comments(adversarial_single)
    masked_single = mask_gdscript_code(cleaned_single)
    elapsed = time.perf_counter() - t0

    assert len(masked_double) == len(adversarial_double)
    assert len(masked_single) == len(adversarial_single)
    assert elapsed < 1.0


def test_await_in_hot_loop_detection_and_lambda_exclusion(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "async_actor.gd").write_text(
        """extends Node

func _ready() -> void:
    await get_tree().process_frame

func _process(delta: float) -> void:
    await get_tree().create_timer(0.1).timeout
    if delta > 0.5:
        await get_tree().process_frame
    var cb = func():
        await get_tree().create_timer(1.0).timeout
    btn.pressed.connect(func(): await get_tree().process_frame)
    # await get_tree().process_frame
    var s = "await get_tree().process_frame"

func _physics_process(delta: float) -> void:
    await get_tree().physics_frame
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.AWAIT_IN_HOT_LOOP
    ]
    assert len(issues) == 3
    assert all(i.severity == Severity.HIGH for i in issues)
    assert [i.line_number for i in issues] == [7, 9, 17]


def test_debug_print_detection_severities_and_exclusions(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "gameplay.gd").write_text(
        """extends Node

func _ready() -> void:
    print("ready")
    print_debug("dbg")
    print_rich("[b]hi[/b]")
    prints("a", "b")
    printt("a", "b")
    printraw("raw")
    # print("commented")
    var s = "print('in string')"
    var r = r"print('in raw')"
    custom_printer.print("method call")

func _process(delta: float) -> void:
    print("hot unconditional")
    if delta > 1.0:
        print("hot conditional")
    var cb = func():
        print("inside lambda")
""",
        encoding="utf-8",
    )
    (tmp_path / "tool_script.gd").write_text(
        """@tool
extends Node

func _ready() -> void:
    print("allowed in @tool")
""",
        encoding="utf-8",
    )
    (tmp_path / "test_sample.gd").write_text(
        """extends GutTest

func test_something() -> void:
    print("allowed in test script")
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = [
        i for i in GDScriptAnalyzer(project).analyze() if i.category == IssueCategory.DEBUG_PRINT
    ]

    assert all(i.file_path == "gameplay.gd" for i in issues)
    by_line = {i.line_number: i for i in issues}
    assert set(by_line.keys()) == {4, 5, 6, 7, 8, 9, 16, 18, 20}
    assert by_line[16].severity == Severity.MEDIUM
    for info_line in (4, 5, 6, 7, 8, 9, 18, 20):
        assert by_line[info_line].severity == Severity.INFO


def test_unsafe_node_path_detection(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[autoload]\nGameState="*res://game_state.gd"\n',
        encoding="utf-8",
    )
    (tmp_path / "game_state.gd").write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "ui.gd").write_text(
        """extends Control

func _ready() -> void:
    var a = get_node("../../../Player")
    var b = get_node_or_null("../../../../World/Enemy")
    var c = $../../../HUD
    var d = $"../../../Main Camera"
    var e = get_node("/root/MainScene/Player")
    var f = $/root/HardcodedLevel/Spawn
    # Safe paths below:
    var ok1 = get_node("../../Sibling")
    var ok2 = $../../Sibling
    var ok3 = get_node("/root/GameState")
    var ok4 = $/root/GameState/SubNode
    # get_node("../../../Commented")
    var s = "get_node('../../../InString')"
    var s2 = "$/root/InString"
""",
        encoding="utf-8",
    )

    project = GodotProject(tmp_path)
    issues = [
        i
        for i in GDScriptAnalyzer(project).analyze()
        if i.category == IssueCategory.UNSAFE_NODE_PATH
    ]
    assert len(issues) == 6
    assert all(i.severity == Severity.LOW for i in issues)
    assert [i.line_number for i in issues] == [4, 5, 6, 7, 8, 9]
