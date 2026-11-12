# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

- **GUT / GdUnit4 test scripts**: scripts extending `GutTest`, `GdUnitTestSuite` or `"res://addons/gut/test.gd"` no longer produce dead-function findings for `test_*` methods or the `before_each`, `after_each`, `before_all`, `after_all`, `prerun_setup` and `postrun_teardown` hooks, and are no longer reported as orphan assets.
- **`addons/` ignored by default**: the `addons/` folder at the project root is now skipped, as the README already stated. New `--include-addons` flag (and `include-addons` input in `action.yml`) to analyze it. `ext_resource` paths pointing into ignored folders are no longer reported as broken when the file exists.
- **`.gdignore` support**: a folder containing a `.gdignore` file is excluded with all its contents, like Godot does.
- **Dynamically built paths**: a `res://` literal ending in `/` or containing a `%` placeholder (`%s`, `%d`...) now references every resource under that prefix, so `"res://assets/bolas/"` + id or `"res://a/b/c_%s.png" % x` no longer leave their files as orphans. Prefixes need at least two folder segments or a folder plus a partial file name; limits documented in the README.

### Fixed

- **Windows console encoding**: `--format json|markdown|github` printed to the screen no longer crashes with `UnicodeEncodeError` on cp1252 consoles; stdout and stderr are reconfigured to UTF-8.
- **Docs**: README and `action.yml` now match the code (real `--severity` and `--fail-on` values including `never`, no `junit-xml`, no non-existent `-h` flag, real default ignores, `github` format in the action).

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
