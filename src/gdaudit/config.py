"""Configuration and baseline management for gd-audit."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


from gdaudit.models import AuditIssue, IssueCategory

CONFIG_FILE_NAME = ".gd-audit.toml"
PYPROJECT_FILE_NAME = "pyproject.toml"


@dataclass
class AuditConfig:
    """Configuration options parsed from .gd-audit.toml or pyproject.toml."""

    ignore: list[str] = field(default_factory=list)
    entry_scenes: list[str] = field(default_factory=list)
    known_dynamic: list[str] = field(default_factory=list)
    reviewed: list[dict[str, Any] | str] = field(default_factory=list)


def _extract_list_option(section: dict[str, Any], key: str, source_path: Path) -> list[Any]:
    """Validate and extract a list-typed configuration option."""
    if key not in section:
        return []
    raw = section[key]
    if not isinstance(raw, list):
        raise ValueError(f"Invalid configuration in '{source_path}': '{key}' must be a list.")
    return raw


def _parse_config_section(section: dict[str, Any], source_path: Path) -> AuditConfig:
    """Build an AuditConfig from a validated TOML section dictionary."""
    raw_ignore = _extract_list_option(section, "ignore", source_path)
    raw_entry_scenes = _extract_list_option(section, "entry_scenes", source_path)
    raw_known_dynamic = _extract_list_option(section, "known_dynamic", source_path)
    raw_reviewed = _extract_list_option(section, "reviewed", source_path)

    ignore = [str(x) for x in raw_ignore]
    entry_scenes = [str(x) for x in raw_entry_scenes]
    known_dynamic = [str(x) for x in raw_known_dynamic]

    reviewed: list[dict[str, Any] | str] = []
    for item in raw_reviewed:
        if isinstance(item, (dict, str)):
            reviewed.append(item)
        else:
            raise ValueError(
                f"Invalid configuration in '{source_path}': "
                "entries in 'reviewed' must be tables or strings."
            )

    return AuditConfig(
        ignore=ignore,
        entry_scenes=entry_scenes,
        known_dynamic=known_dynamic,
        reviewed=reviewed,
    )


def load_config(project_root: Path) -> AuditConfig:
    """Load and parse .gd-audit.toml (or [tool.gd-audit] in pyproject.toml).

    Returns an empty AuditConfig if neither file defines gd-audit configuration.
    Raises ValueError on invalid TOML syntax or malformed structure.
    """
    config_path = project_root / CONFIG_FILE_NAME
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML syntax in '{config_path}': {exc}") from exc

        section: dict[str, Any] = data
        if "audit" in data:
            if not isinstance(data["audit"], dict):
                raise ValueError(
                    f"Invalid configuration in '{config_path}': '[audit]' must be a table."
                )
            section = {**data["audit"], **data}
        elif "tool" in data:
            tool_table = data["tool"]
            if not isinstance(tool_table, dict):
                raise ValueError(
                    f"Invalid configuration in '{config_path}': '[tool]' must be a table."
                )
            if "gd-audit" in tool_table:
                if not isinstance(tool_table["gd-audit"], dict):
                    raise ValueError(
                        f"Invalid configuration in '{config_path}': "
                        "'[tool.gd-audit]' must be a table."
                    )
                section = {**tool_table["gd-audit"], **data}

        return _parse_config_section(section, config_path)

    pyproject_path = project_root / PYPROJECT_FILE_NAME
    if pyproject_path.is_file():
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML syntax in '{pyproject_path}': {exc}") from exc

        if "tool" in data:
            tool_table = data["tool"]
            if not isinstance(tool_table, dict):
                raise ValueError(
                    f"Invalid configuration in '{pyproject_path}': '[tool]' must be a table."
                )
            if "gd-audit" in tool_table:
                gd_section = tool_table["gd-audit"]
                if not isinstance(gd_section, dict):
                    raise ValueError(
                        f"Invalid configuration in '{pyproject_path}': "
                        "'[tool.gd-audit]' must be a table."
                    )
                return _parse_config_section(gd_section, pyproject_path)

    return AuditConfig()


def normalize_rel_path(raw_path: str | None) -> str:
    """Normalize a path to a forward-slash relative representation without res://."""
    if not raw_path:
        return ""
    clean = raw_path.strip().replace("\\", "/")
    if clean.startswith("res://"):
        clean = clean[len("res://") :].lstrip("/")
    return clean.removeprefix("./")


def issue_matches_reviewed(issue: AuditIssue, entry: dict[str, Any] | str) -> bool:
    """Return True if an issue matches a reviewed entry in .gd-audit.toml."""
    issue_file = normalize_rel_path(issue.file_path)
    issue_cat = (
        issue.category.value if hasattr(issue.category, "value") else str(issue.category)
    ).lower()
    issue_title = issue.title.strip().lower()

    if isinstance(entry, dict):
        has_filter = False

        # Target path/file
        target_path = entry.get("file") or entry.get("path") or entry.get("file_path")
        if target_path is not None:
            has_filter = True
            norm_target = normalize_rel_path(str(target_path))
            if not (
                issue_file == norm_target
                or fnmatch.fnmatch(issue_file, norm_target)
                or (issue.file_path and fnmatch.fnmatch(issue.file_path, str(target_path)))
            ):
                return False

        # Category
        if "category" in entry and entry["category"] is not None:
            has_filter = True
            target_cat = str(entry["category"]).strip().lower()
            if target_cat != issue_cat:
                return False

        # Title
        if "title" in entry and entry["title"] is not None:
            has_filter = True
            target_title = str(entry["title"]).strip().lower()
            if not (
                target_title == issue_title
                or target_title in issue_title
                or fnmatch.fnmatch(issue.title, str(entry["title"]))
            ):
                return False

        # Line number
        if "line" in entry or "line_number" in entry:
            raw_line = entry.get("line") if "line" in entry else entry.get("line_number")
            if raw_line is not None:
                has_filter = True
                try:
                    target_line = int(raw_line)
                    if issue.line_number != target_line:
                        return False
                except (ValueError, TypeError):
                    return False

        return has_filter

    if isinstance(entry, str):
        clean_entry = entry.strip()
        if not clean_entry:
            return False

        # Category prefix notation: "orphan_asset: assets/icon.png"
        if ":" in clean_entry and not clean_entry.startswith("res://"):
            cat_part, rest = clean_entry.split(":", 1)
            cat_part = cat_part.strip().lower()
            rest = rest.strip()
            if cat_part == issue_cat:
                norm_rest = normalize_rel_path(rest)
                if (
                    issue_file == norm_rest
                    or fnmatch.fnmatch(issue_file, norm_rest)
                    or rest.lower() in issue_title
                ):
                    return True

        norm_entry = normalize_rel_path(clean_entry)
        if issue_file and (issue_file == norm_entry or fnmatch.fnmatch(issue_file, norm_entry)):
            return True

        entry_lower = clean_entry.lower()
        if entry_lower == issue_title or entry_lower in issue_title:
            return True

    return False


def filter_issues(issues: list[AuditIssue], config: AuditConfig) -> list[AuditIssue]:
    """Filter issues using configuration rules: ignore, entry_scenes, known_dynamic, reviewed."""
    # Pre-process entry scenes into normalized paths and res:// URIs
    entry_scene_norms: set[str] = set()
    for s in config.entry_scenes:
        norm = normalize_rel_path(s)
        if norm:
            entry_scene_norms.add(norm)

    # Pre-process known dynamic function names
    known_dyn_names: set[str] = {fn.strip() for fn in config.known_dynamic if fn.strip()}

    filtered: list[AuditIssue] = []
    for issue in issues:
        norm_file = normalize_rel_path(issue.file_path)
        cat_str = (
            issue.category.value if hasattr(issue.category, "value") else str(issue.category)
        ).lower()

        # 1. Ignore patterns check
        if norm_file:
            is_ignored = False
            for pat in config.ignore:
                clean_pat = normalize_rel_path(pat)
                if not clean_pat:
                    continue
                if fnmatch.fnmatch(norm_file, clean_pat) or (
                    issue.file_path and fnmatch.fnmatch(issue.file_path, pat)
                ):
                    is_ignored = True
                    break
                pat_dir = clean_pat if clean_pat.endswith("/") else f"{clean_pat}/"
                if norm_file.startswith(pat_dir):
                    is_ignored = True
                    break
            if is_ignored:
                continue

        # 2. Entry scenes check for orphan_asset
        if cat_str == IssueCategory.ORPHAN_ASSET.value and norm_file in entry_scene_norms:
            continue

        # 3. Known dynamic check for dead_function
        if cat_str == IssueCategory.DEAD_FUNCTION.value:
            is_dynamic = False
            for dyn_fn in known_dyn_names:
                # Match title candidate: "Dead Function Candidate: 'dyn_fn'"
                if f"'{dyn_fn}'" in issue.title or (
                    issue.context and issue.context.startswith(f"func {dyn_fn}(")
                ):
                    is_dynamic = True
                    break
            if is_dynamic:
                continue

        # 4. Reviewed accepted findings
        if any(issue_matches_reviewed(issue, r) for r in config.reviewed):
            continue

        filtered.append(issue)

    return filtered


def issue_fingerprint(issue: AuditIssue) -> str:
    """Generate a deterministic SHA-256 fingerprint for an issue.

    Includes normalized context so distinct occurrences in the same file with the
    same category and title do not collide, while remaining resilient to line shifts.
    """
    norm_path = normalize_rel_path(issue.file_path)
    cat_str = (
        issue.category.value if hasattr(issue.category, "value") else str(issue.category)
    ).lower()
    title_str = issue.title.strip()
    context_str = (issue.context or "").strip()
    raw = f"{norm_path}::{cat_str}::{title_str}::{context_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_tuple(issue: AuditIssue) -> tuple[str, str, str]:
    """Return the identifying tuple (file_path, category, title) for an issue."""
    norm_path = normalize_rel_path(issue.file_path)
    cat_str = (
        issue.category.value if hasattr(issue.category, "value") else str(issue.category)
    ).lower()
    return (norm_path, cat_str, issue.title.strip())


def save_baseline(baseline_path: Path, issues: list[AuditIssue]) -> None:
    """Save issues to a baseline JSON file."""
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for issue in issues:
        cat_str = (
            issue.category.value if hasattr(issue.category, "value") else str(issue.category)
        ).lower()
        records.append(
            {
                "file_path": normalize_rel_path(issue.file_path),
                "line_number": issue.line_number,
                "category": cat_str,
                "title": issue.title,
                "context": (issue.context or "").strip(),
                "fingerprint": issue_fingerprint(issue),
            }
        )

    payload = {
        "version": 1,
        "issue_count": len(records),
        "issues": records,
    }
    baseline_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_baseline(baseline_path: Path) -> tuple[set[tuple[str, str, str]], set[str]]:
    """Load baseline issues, returning (set of identifier tuples, set of fingerprints).

    Raises ValueError if the baseline file contains invalid JSON or an unsupported structure.
    """
    if not baseline_path.is_file():
        return set(), set()

    content = baseline_path.read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in baseline file '{baseline_path}': {exc}") from exc

    raw_issues: list[Any]
    if isinstance(data, dict):
        if "issues" in data:
            raw_val = data["issues"]
        elif "fingerprints" in data:
            raw_val = data["fingerprints"]
        else:
            raise ValueError(
                f"Invalid baseline structure in '{baseline_path}': "
                "expected 'issues' or 'fingerprints' list."
            )
        if not isinstance(raw_val, list):
            raise ValueError(
                f"Invalid baseline structure in '{baseline_path}': "
                "'issues'/'fingerprints' must be a list."
            )
        raw_issues = raw_val
    elif isinstance(data, list):
        raw_issues = data
    else:
        raise ValueError(
            f"Invalid baseline structure in '{baseline_path}': expected a JSON object or list."
        )

    baseline_tuples: set[tuple[str, str, str]] = set()
    baseline_fingerprints: set[str] = set()

    for item in raw_issues:
        if isinstance(item, dict):
            file_path = normalize_rel_path(
                item.get("file_path") or item.get("file") or item.get("path")
            )
            category = str(item.get("category", "")).strip().lower()
            title = str(item.get("title", "")).strip()
            context = str(item.get("context") or "").strip()
            baseline_tuples.add((file_path, category, title))

            fp = item.get("fingerprint")
            if fp and isinstance(fp, str):
                baseline_fingerprints.add(fp)
            elif file_path or category or title:
                raw = f"{file_path}::{category}::{title}::{context}"
                baseline_fingerprints.add(hashlib.sha256(raw.encode("utf-8")).hexdigest())
        elif isinstance(item, str):
            baseline_fingerprints.add(item)
        else:
            raise ValueError(
                f"Invalid baseline entry in '{baseline_path}': "
                "entries must be JSON objects or fingerprint strings."
            )

    return baseline_tuples, baseline_fingerprints


def filter_by_baseline(
    issues: list[AuditIssue],
    baseline_tuples: set[tuple[str, str, str]],
    baseline_fingerprints: set[str] | None = None,
) -> list[AuditIssue]:
    """Filter out issues present in the baseline, leaving only new issues."""
    fps = baseline_fingerprints or set()
    use_fingerprints = bool(fps)
    new_issues: list[AuditIssue] = []

    for issue in issues:
        if use_fingerprints:
            if issue_fingerprint(issue) in fps:
                continue
        elif issue_tuple(issue) in baseline_tuples:
            continue
        new_issues.append(issue)

    return new_issues
