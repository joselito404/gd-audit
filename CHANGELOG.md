# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **Hot loop performance detectors**: Expanded detection inside continuous update loops (`_process` / `_physics_process`) for `RegEx.new()` / `RegEx.create_from_string()`, `FileAccess.open()` (and direct read variants), `DirAccess.open()`, `get_tree().get_nodes_in_group()` / `get_nodes_in_group()`, and dynamic object instantiations (`.instantiate()` / `.new()`).
- **1-level indirect call tracking**: Hot loop auditor traces helper functions called directly from `_process` or `_physics_process` (e.g. `_process -> helper_func`), flagging nested expensive operations as `low` severity with the complete invocation chain in the message.
- **Configuration file (`.gd-audit.toml`)**: Support for project-root configuration with `ignore` (globs/paths), `entry_scenes` (scenes exempted from orphan detection), `known_dynamic` (reflection/dynamic methods exempted from dead code analysis), and `reviewed` (accepted and suppressed findings).
- **Baseline mode (`--baseline` / `-b`)**: Enables gradual adoption for projects with pre-existing technical debt. Saves existing findings on initial run and reports only new issues or regressions on subsequent runs.
- **SARIF 2.1.0 report format (`--format sarif`)**: Added SARIF output format for native integration with GitHub Code Scanning (`upload-sarif`).

### Changed

- **Early CLI option validation**: Pre-validation of `--format`, `--severity`, and `--fail-on` before any filesystem access or project discovery, immediately terminating with exit code 2 on invalid values.
- **Expanded test suite & coverage**: Expanded test suite to 234+ tests with 95% global code coverage (100% in `autoloads`, `models`, and `cli`).
- **GUT / GdUnit4 test scripts**: Scripts extending `GutTest`, `GdUnitTestSuite` or `"res://addons/gut/test.gd"` no longer produce dead-function findings for `test_*` methods or the `before_each`, `after_each`, `before_all`, `after_all`, `prerun_setup` and `postrun_teardown` hooks, and are no longer reported as orphan assets.
- **`addons/` ignored by default**: The `addons/` folder at the project root is now skipped by default. Added `--include-addons` flag (and `include-addons` input in `action.yml`) to analyze it. `ext_resource` paths pointing into ignored folders are no longer reported as broken when the file exists.
- **`.gdignore` support**: Folders containing a `.gdignore` file are excluded with all their contents, mirroring Godot behavior.
- **Dynamically built paths**: A `res://` literal ending in `/` or containing a `%` placeholder (`%s`, `%d`...) now references every resource under that prefix (e.g. `"res://assets/enemies/"` or `"res://a/b/c_%s.png" % x`), eliminating false positive orphan warnings.

### Fixed

- **Orphan assets and `class_name`**: A script that declares `class_name X` is no longer reported as an orphan when `X` appears as a whole word in another `.gd`, `.tscn`, `.tres` or `project.godot` file, as in `X.new()`, `var x: X` or a scene node with `type="X"`.
- **Invalid `--fail-on`**: A wrong value prints an informative error message listing valid options and exits with code 2 instead of a `ValueError` traceback.
- **Windows console encoding**: Console output for `--format json|markdown|github` no longer crashes with `UnicodeEncodeError` on cp1252 consoles; stdout and stderr are reconfigured to UTF-8.
- **Documentation and action alignment**: README and `action.yml` updated to match actual CLI options, default ignores, and supported features.

---

## [0.1.0] - 2026-11-10

### Added

- **Autoload Cycle Analyzer**:
  - Directed Acyclic Graph (DAG) construction of singletons defined in `project.godot`.
  - Tarjan / DFS cycle detection flagging mutual dependencies that cause initialization deadlocks or silent `null` states.

- **Scene & Resource Validator**:
  - Full static parser for Godot 4 scene (`.tscn`) and resource (`.tres`) files.
  - Validation of external dependencies (`ext_resource`) against physical disk paths.
  - Detection of broken script attachments, missing sub-resources, and invalid resource UID links.

- **Performance & Hot Loop Auditor**:
  - GDScript AST inspection targeting continuous game loop callbacks (`_process` and `_physics_process`).
  - Detection of expensive real-time operations inside hot loops, including repeated `get_node()` queries, string node path resolutions, regex evaluations, and file I/O operations.

- **Dead Code & Asset Hygiene**:
  - Orphaned asset scanner finding unused textures, audio files, and resources that bloat project export packages.
  - Audit of uncalled local helper methods and disconnected signal targets.

- **CLI & Output Engine**:
  - Intuitive CLI powered by `argparse` / `click` with configurable path scanning and glob-based ignore rules (`--ignore-paths`).
  - Multi-tiered severity classification: `info`, `warning`, `high`, and `critical`.
  - Configurable exit code threshold via `--fail-on` for CI/CD gates.
  - Rich formatted console reports with tables and visual highlights.
  - Machine-readable output formats: `json`, `markdown`, and `github` annotations.
  - Support for instant execution with `uvx gd-audit .` without prior local installation.
