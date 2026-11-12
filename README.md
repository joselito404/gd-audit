# gd-audit

> **Static architecture linter and dependency health analyzer for Godot 4 projects**

[![CI](https://github.com/joselito404/gd-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/joselito404/gd-audit/actions)
[![Release](https://img.shields.io/github/v/release/joselito404/gd-audit?color=blue&logo=github)](https://github.com/joselito404/gd-audit/releases)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Godot 4+](https://img.shields.io/badge/godot-4.x-478cbf.svg?logo=godotengine&logoColor=white)](https://godotengine.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

**gd-audit** analyzes Godot 4 project architecture, scene trees, GDScript dependencies, and resource references. Unlike standard syntax linters, it focuses on structural bugs, circular initialization deadlocks, dangling scene dependencies, and performance bottlenecks inside continuous update loops.

---

## 🚀 Quickstart

Run immediately with zero installation via [uv](https://docs.astral.sh/uv/):

```bash
uvx gd-audit .
```

Or install globally via [pipx](https://pypa.github.io/pipx/) or standard pip:

```bash
pipx install gd-audit
# or
pip install gd-audit
```

Audit your Godot project root:

```bash
gd-audit path/to/godot/project
```

---

## 💡 Why gd-audit?

Standard Godot linters (such as `gdlint`) are formatting tools that ensure code style consistency: line length, indentation, trailing commas, or variable naming conventions.

While clean style is valuable, style linters do not prevent architectural failures:

| Problem in Godot Projects | Standard Style Linters | **gd-audit** |
| :--- | :---: | :---: |
| Indentation & whitespace formatting | ✅ | ➖ *(delegates to style linters)* |
| **Autoload circular dependencies** (silent `null` or initialization lock) | ❌ | ✅ **Detected (Graph cycle analysis)** |
| **Broken external `.tscn` / `.tres` paths** (crashes exported builds) | ❌ | ✅ **Validated statically** |
| **Orphaned / unused assets** bloating game builds | ❌ | ✅ **Reported with byte footprint** |
| **Expensive operations in `_process` / `_physics_process`** | ❌ | ✅ **Flagged with severity score** |
| **Dead code & unreachable signal callbacks** | ❌ | ✅ **Audited across scenes** |

`gd-audit` treats your Godot repository as an interconnected dependency graph, catching breaking architectural defects before you run or export your game.

---

## ✨ Features

- 🔄 **Autoload Dependency Graph & Cycle Detection**: Builds a complete directed acyclic graph (DAG) of project singletons. Identifies dependency loops and circular initialization deadlocks before they trigger runtime errors.
- 📦 **Scene & Resource Validator**: Deep-scans `.tscn`, `.tres`, and project configuration files to catch broken external resource IDs (`ext_resource`), missing node script attachments, and unreferenced declared assets.
- ⚡ **GDScript Performance & Dead Code Scanner**: Identifies expensive queries (e.g. repeated `get_node()`, dynamic string node retrieval, regex parsing, file I/O) executed inside hot loops (`_process` / `_physics_process`), as well as unreferenced local methods.
- 🎨 **Beautiful Terminal Output & CI Ready**: Features rich tabular console reports with syntax highlighting, alongside machine-readable formats (`json`, `markdown`, `github` annotations) designed for continuous integration pipelines.

---

## 🛠️ CLI Flags & Options

```bash
gd-audit [PROJECT_PATH] [OPTIONS]
```

### Options Reference

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | Positional | `.` | Root directory of the Godot project (containing `project.godot`) or a subfolder. |
| `--severity`, `-s` | Choice (`info`, `low`, `medium`, `high`, `critical`) | `low` | Minimum severity to display in the output report. |
| `--fail-on` | Choice (`info`, `low`, `medium`, `high`, `critical`, `never`) | `high` | Returns exit code `1` if issues at or above this severity are detected. `never` always returns `0`. |
| `--format`, `-f` | Choice (`rich`, `json`, `markdown`, `github`) | `rich` | Output presentation format. `github` generates workflow annotations. |
| `--output`, `-o` | File Path | stdout | Write the report to a file instead of (or, with `rich`, in addition to) the screen. |
| `--ignore-paths`, `-i` | Comma- or space-separated strings | none | Extra paths or glob patterns to exclude from analysis (added to the default ignores below). |
| `--check-orphans` / `--no-check-orphans` | Boolean Flag | `--check-orphans` | Scan for resources and textures never referenced by any scene or script. |
| `--include-addons` | Boolean Flag | `false` | Also analyze the `addons/` folder at the project root, which is ignored by default. |
| `--version`, `-v` | Flag | — | Print `gd-audit` version and exit. |
| `--help` | Flag | — | Display CLI help message and usage manual. |

### Ignored by default

Like Godot itself, these are never scanned (so they never produce findings nor count as references):

- `.godot/`, `.git/`, `.import/`, `.github/`, `__pycache__/`, `.venv/`, `venv/`, `node_modules/` and the pytest, ruff and mypy caches, at any depth.
- `addons/` at the **project root** (a folder named `addons` deeper in the tree is analyzed). Use `--include-addons` to analyze it.
- Any folder that contains a `.gdignore` file, together with everything below it (for example a `capturas/` folder of screenshots).

### Test scripts

Scripts that extend `GutTest`, `GdUnitTestSuite` or `"res://addons/gut/test.gd"` are test files: their `test_*` methods and the `before_each`, `after_each`, `before_all`, `after_all`, `prerun_setup` and `postrun_teardown` hooks are not reported as dead functions, and the files themselves are not reported as orphan assets.

### Limits

- **Dynamic paths.** A `res://` literal is treated as a reference by *prefix* when it ends in `/` (a folder to which an id is appended at runtime) or contains a `%` placeholder (`%s`, `%d`...). The prefix is the folder, or the text before the first `%`, and every resource whose path starts with it counts as referenced. To avoid silencing the whole project, the prefix must have at least two folder segments or one folder plus a partial file name: `res://assets/bolas/` and `res://assets/ruleta/3d/casilla_tienda%s.glb` count, `res://assets/` and `res://` do not. Paths built with other string concatenations (`"res://assets/" + name`) are not detected.
- **Class names.** A script that declares `class_name X` is referenced when `X` appears as a whole word in another `.gd`, `.tscn`, `.tres` or `project.godot` file (comments and strings included), for example `X.new()`, `var x: X` or a scene node with `type="X"`. The script's own mentions do not count, and a commented-out `class_name` is not a declaration.
- **Test detection** looks for a direct `extends GutTest` (or the other forms above); scripts that inherit from your own base test class are not recognized as tests.
- **Dead functions and orphan assets are candidates**, found by name and path matching without running the game; review them before deleting anything.

---

## 🤖 Continuous Integration (GitHub Actions)

Add `gd-audit` to your automated validation pipeline to safeguard pull requests against architectural regressions:

```yaml
name: GD-Audit

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  audit:
    name: Architecture & Dependency Health
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run gd-audit
        run: pipx run gd-audit . --fail-on high --format github
```

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
