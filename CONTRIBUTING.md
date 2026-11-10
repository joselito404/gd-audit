# Contributing to gd-audit

Thank you for your interest in improving **gd-audit**! We welcome bug reports, feature requests, documentation enhancements, and code contributions from the Godot and Python communities.

---

## 🛠️ Development Setup

`gd-audit` is built with modern Python (3.10+) and uses [uv](https://docs.astral.sh/uv/) for lightning-fast virtual environment and dependency management.

### 1. Clone the Repository

```bash
git clone https://github.com/jose/gd-audit.git
cd gd-audit
```

### 2. Set Up Virtual Environment with uv

```bash
# Create a local virtual environment (.venv)
uv venv

# Activate virtual environment
# On Linux / macOS:
source .venv/bin/activate
# On Windows (cmd):
.venv\Scripts\activate.bat
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

### 3. Install Development Dependencies

Install the project in editable mode including development toolchains:

```bash
uv pip install -e ".[dev]"
```

---

## 🧪 Testing and Quality Control

All submissions must pass linting, formatting, and unit tests before merging.

### Running Tests

We use `pytest` with test fixtures simulating various Godot 4 project layouts (autoload cycles, missing resource references, hot loop violations):

```bash
# Run the complete test suite
pytest

# Run tests with verbose output and coverage
pytest -v --cov=gd_audit
```

### Linting and Formatting

We enforce strict formatting and code quality rules using [ruff](https://docs.astral.sh/ruff/):

```bash
# Check code rules and import ordering
ruff check .

# Automatically apply safe fixes
ruff check . --fix

# Format code according to repository standards
ruff format .
```

### Type Checking

We maintain strict type annotations across the codebase:

```bash
mypy src/gd_audit
```

---

## 🌿 Contribution Workflow

1. **Fork & Branch**: Create a focused branch from `main`:
   ```bash
   git checkout -b feat/autoload-cycle-visualizer
   ```
2. **Follow Code Conventions**:
   - Write clear docstrings and comprehensive type hints.
   - Keep Godot parser logic robust against malformed or unsupported syntax.
   - Add targeted test cases under `tests/` reproducing any reported issue or new feature.
3. **Commit Messages**: We encourage conventional commit messages:
   - `feat: add detection for unreferenced custom resources`
   - `fix: resolve crash on multiline string parsing in .tscn`
   - `docs: update CLI flag reference in README`
4. **Submit a Pull Request**:
   - Provide a clear description of the problem solved.
   - Reference any related issue number (`Fixes #12`).
   - Verify all CI checks pass on GitHub Actions.

---

## 💬 Community & Governance

- Please maintain a respectful and welcoming environment for all contributors.
- If you encounter a security vulnerability or critical bug, please open a confidential advisory or contact the maintainers directly.
