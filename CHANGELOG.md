# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
