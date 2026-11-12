"""Packaging guards: every runtime import must be covered by a declared dependency."""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_tomli_is_a_runtime_dependency_for_python_below_311() -> None:
    """config.py imports tomli on Python < 3.11; without it the CLI cannot start there.

    CI never notices because pytest pulls tomli in on Python 3.10, so declare it explicitly.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]

    tomli_entries = [dep for dep in dependencies if re.match(r"tomli\b", dep)]

    assert tomli_entries, "tomli must be declared in [project].dependencies"
    marker = tomli_entries[0].replace('"', "'")
    assert "python_version < '3.11'" in marker
