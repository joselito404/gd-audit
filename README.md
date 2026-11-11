# gd-audit

> **Static architecture linter and dependency health analyzer for Godot 4 projects**

[![CI](https://github.com/joselito404/gd-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/joselito404/gd-audit/actions)
[![PyPI version](https://img.shields.io/pypi/v/gd-audit.svg)](https://pypi.org/project/gd-audit/)
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
- 🎨 **Beautiful Terminal Output & CI Ready**: Features rich tabular console reports with syntax highlighting, alongside machine-readable formats (`json`, `markdown`, `junit-xml`) designed for continuous integration pipelines.

---

## 🛠️ CLI Flags & Options

```bash
gd-audit [PROJECT_PATH] [OPTIONS]
```

### Options Reference

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | Positional | `.` | Root directory of the Godot project (containing `project.godot`). |
| `--severity` | Choice (`info`, `warning`, `high`, `critical`) | `info` | Minimum severity threshold to display in the output report. |
| `--fail-on` | Choice (`info`, `warning`, `high`, `critical`, `none`) | `high` | Returns exit code `1` if issues at or above this severity are detected. |
| `--format` | Choice (`rich`, `json`, `markdown`, `github`) | `rich` | Output presentation format. `github` generates workflow annotations. |
| `--output`, `-o` | File Path | `stdout` | Write output report to a specific file instead of stdout. |
| `--ignore-paths` | Comma-separated strings | `.godot/*,addons/*` | Glob patterns of paths or directories to exclude from analysis. |
| `--check-orphans` | Boolean Flag | `true` | Scan for resources and textures never referenced by any scene or script. |
| `--version`, `-v` | Flag | — | Print `gd-audit` version and exit. |
| `--help`, `-h` | Flag | — | Display CLI help message and usage manual. |

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
