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

Run directly without local installation via [pipx](https://pypa.github.io/pipx/):

```bash
pipx run --spec git+https://github.com/joselito404/gd-audit gd-audit .
```

Or install using standard `pip`:

```bash
pip install git+https://github.com/joselito404/gd-audit
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
- ⚡ **GDScript Performance & Hot Loop Auditor**: Identifies expensive queries and dynamic allocations executed inside continuous update loops (`_process` / `_physics_process`), with 1-level indirect call tracking.
- 🧹 **Dead Code & Asset Hygiene**: Detects uncalled local methods, unreferenced functions, and orphaned assets bloating export packages.
- ⚙️ **Project Configuration & Baselines**: Supports `.gd-audit.toml` for fine-grained suppression rules and `--baseline` for gradual adoption in projects with pre-existing technical debt.
- 🎨 **Terminal Reports & CI Integrations**: Features rich console reports alongside machine-readable formats (`json`, `markdown`, `github` workflow annotations, and `sarif` for GitHub Code Scanning).

---

## ⚡ Performance Detectors (Hot Loops)

Executing heavy operations inside `_process()` or `_physics_process()` causes stutter and frame drops. `gd-audit` flags anti-patterns inside these continuous loops:

### Direct Hot Loop Patterns (Medium Severity)

- **Node hierarchy queries**: `get_node()`, `get_node_or_null()`, `$Node` shorthand, `find_child()`, `find_parent()`, `find_children()`. Cache these during `_ready()` or with `@onready`.
- **Dynamic resource loading**: `ResourceLoader.load()`, `load()`. Use `preload()` or load assets asynchronously.
- **RegEx compilation**: `RegEx.new()`, `RegEx.create_from_string()`. Compile patterns once during initialization.
- **File & Directory I/O**: `FileAccess.open()` (and direct read variants), `DirAccess.open()`. Disk access blocks the main thread.
- **Tree group lookups**: `get_tree().get_nodes_in_group()`, `get_nodes_in_group()`. Query groups outside hot loops or subscribe with signals.
- **Dynamic object instantiations**: `.instantiate()`, `.new()`. Creates garbage collection pressure; use object pooling.

### Indirect Call Tracking (Low Severity)

`gd-audit` performs 1-level indirect call analysis: if `_process()` or `_physics_process()` calls a local helper function (e.g. `_process -> helper_func`), and that helper performs any of the expensive operations above, `gd-audit` flags the issue with `low` severity, highlighting the exact invocation chain (`_process -> helper_func`) and line numbers. The indirect tracing only follows calls that run on every frame without an `if`/`elif`/`else`/`match` protecting them (el rastreo indirecto solo sigue llamadas que se ejecutan en cada fotograma sin `if`/`elif`/`else`/`match` que las proteja); calls inside `for`/`while` loops still count, while calls guarded by a condition (e.g. `if timer >= interval: spawn()`) are not reported.

---

## ⚙️ Configuration (`.gd-audit.toml`)

You can customize `gd-audit` behavior by adding a `.gd-audit.toml` file at the root of your Godot project. It supports 4 configuration keys:

- `ignore`: List of file paths or glob patterns to exclude from analysis.
- `entry_scenes`: Scenes launched via CLI, dynamically loaded, or used as entry points that should not be flagged as orphan assets.
- `known_dynamic`: Method names invoked dynamically (reflection, signals, RPCs) that should not be flagged as dead code.
- `reviewed`: Accepted and justified findings suppressed from reports. Supports table dictionaries (with `file`/`path`, `category`, `title`, `line`) or string entries.

### Example `.gd-audit.toml`

```toml
# .gd-audit.toml (placed in Godot project root)

# Glob patterns or directories to ignore
ignore = [
    "thirdparty/**",
    "assets/raw/**",
]

# Scenes loaded dynamically or via CLI that are not orphans
entry_scenes = [
    "scenes/main_menu.tscn",
    "scenes/levels/intro.tscn",
]

# Methods called dynamically (reflection, signals, call())
known_dynamic = [
    "on_custom_event",
    "_handle_network_payload",
]

# Accepted and justified findings suppressed from reports
reviewed = [
    { file = "scripts/utils/logger.gd", category = "dead_function", title = "Dead Function Candidate: 'debug_dump'" },
    "orphan_asset: assets/placeholder.png",
]
```

---

## 📈 Baseline Mode (`--baseline` / `-b`)

When adopting `gd-audit` on an established project with existing technical debt, you can use baseline mode to prevent regressions without having to fix all historical issues immediately:

1. **Generate baseline** (records all current issues and returns exit code 0):
   ```bash
   gd-audit . --baseline .gd-audit-baseline.json
   ```

2. **Run subsequent audits** (suppresses recorded baseline issues and reports only new issues):
   ```bash
   gd-audit . --baseline .gd-audit-baseline.json --fail-on high
   ```

Commit `.gd-audit-baseline.json` to version control so pull requests only fail when introducing new issues.

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
| `--format`, `-f` | Choice (`rich`, `json`, `markdown`, `github`, `sarif`) | `rich` | Output presentation format. `github` generates workflow annotations, `sarif` outputs SARIF 2.1.0 for GitHub Code Scanning. |
| `--output`, `-o` | File Path | stdout | Write the report to a file instead of (or, with `rich`, in addition to) the screen. |
| `--baseline`, `-b` | File Path | none | Baseline file path: records findings on first run, suppresses them on subsequent runs to flag only regressions. |
| `--ignore-paths`, `-i` | Comma- or space-separated strings | none | Extra paths or glob patterns to exclude from analysis (added to the default ignores). |
| `--check-orphans` / `--no-check-orphans` | Boolean Flag | `--check-orphans` | Scan for resources and textures never referenced by any scene or script. |
| `--include-addons` | Boolean Flag | `false` | Also analyze the `addons/` folder at the project root, which is ignored by default. |
| `--version`, `-v` | Flag | — | Print `gd-audit` version and exit. |
| `--help` | Flag | — | Display CLI help message and usage manual. |

### Ignored by Default

Like Godot itself, these are never scanned (so they never produce findings nor count as references):

- `.godot/`, `.git/`, `.import/`, `.github/`, `__pycache__/`, `.venv/`, `venv/`, `node_modules/` and the pytest, ruff and mypy caches, at any depth.
- `addons/` at the **project root** (a folder named `addons` deeper in the tree is analyzed). Use `--include-addons` to analyze it.
- Any folder that contains a `.gdignore` file, together with everything below it.

### Test Scripts

Scripts that extend `GutTest`, `GdUnitTestSuite` or `"res://addons/gut/test.gd"` are treated as test files: their `test_*` methods and lifecycle hooks (`before_each`, `after_each`, `before_all`, `after_all`, `prerun_setup`, `postrun_teardown`) are not reported as dead functions, and the test files themselves are not reported as orphan assets.

### Limits

- **Dynamic paths.** A `res://` literal is treated as a reference by *prefix* when it ends in `/` (a folder to which an identifier is appended at runtime) or contains a `%` placeholder (`%s`, `%d`...). The prefix is the folder, or the text before the first `%`, and every resource whose path starts with it counts as referenced. To avoid silencing the whole project, the prefix must have at least two folder segments or one folder plus a partial file name: `res://assets/enemies/` and `res://assets/characters/3d/skin_%s.glb` count, `res://assets/` and `res://` do not. Paths built with other string concatenations (`"res://assets/" + name`) are not detected.
- **Class names.** A script that declares `class_name X` is referenced when `X` appears as a whole word in another `.gd`, `.tscn`, `.tres` or `project.godot` file (comments and strings included), for example `X.new()`, `var x: X` or a scene node with `type="X"`. The script's own mentions do not count, and a commented-out `class_name` is not a declaration.
- **Test detection** looks for a direct `extends GutTest` (or the other forms above); scripts that inherit from your own base test class are not recognized as tests.
- **Dead functions and orphan assets are candidates**, found by name and path matching without running the game; review them before deleting anything.

---

## 🤖 Continuous Integration (GitHub Actions)

### Option A: Using the Composite Action

You can reference the composite action directly from GitHub:

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

      - name: Run gd-audit action
        uses: joselito404/gd-audit@main
        with:
          path: "."
          severity: "low"
          fail-on: "high"
          format: "github"
```

### Option B: GitHub Code Scanning (SARIF Integration)

To integrate findings into the GitHub Security tab via Code Scanning:

```yaml
name: GD-Audit Security & Code Scanning

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  audit:
    name: GD-Audit SARIF
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Run gd-audit and generate SARIF report
        run: |
          pipx run --spec git+https://github.com/joselito404/gd-audit gd-audit . --format sarif --output results.sarif --fail-on never

      - name: Upload SARIF to GitHub Code Scanning
        uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: results.sarif
```

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
