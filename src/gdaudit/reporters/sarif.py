"""SARIF 2.1.0 reporter for gd-audit results.

Generates SARIF 2.1.0 (Static Analysis Results Interchange Format) valid
for GitHub Code Scanning (github/codeql-action/upload-sarif).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gdaudit import __version__
from gdaudit.models import AuditIssue, AuditResult, IssueCategory, Severity

SARIF_SCHEMA: str = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)
SARIF_VERSION: str = "2.1.0"
INFORMATION_URI: str = "https://github.com/joselito404/gd-audit"

SEVERITY_TO_SARIF_LEVEL: dict[Severity, str] = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "warning",
    Severity.INFO: "note",
}

KNOWN_RULE_DESCRIPTIONS: dict[str, str] = {
    IssueCategory.AUTOLOAD_CYCLE.value: "Circular autoload dependency detected.",
    IssueCategory.UNUSED_AUTOLOAD.value: "Autoload registered but never referenced.",
    IssueCategory.BROKEN_RESOURCE.value: "External resource reference could not be resolved.",
    IssueCategory.UNUSED_RESOURCE.value: "Resource file is not referenced anywhere.",
    IssueCategory.ORPHAN_ASSET.value: "Asset file is not referenced anywhere in the project.",
    IssueCategory.DEAD_FUNCTION.value: "GDScript function defined but never called.",
    IssueCategory.PERFORMANCE_ANTIPATTERN.value: "Performance anti-pattern detected in hot loop.",
}


def _severity_to_sarif_level(sev: Severity | str) -> str:
    """Map an AuditSeverity level to SARIF 2.1.0 level: error, warning, note."""
    if isinstance(sev, str) and not isinstance(sev, Severity):
        try:
            sev = Severity.from_string(sev)
        except ValueError:
            return "warning"
    return SEVERITY_TO_SARIF_LEVEL.get(sev, "warning")


def _to_rule_id(category: IssueCategory | str) -> str:
    """Return the stable rule ID string for an issue category."""
    if isinstance(category, IssueCategory):
        return category.value
    return str(category)


def _to_rule_name(category: IssueCategory | str) -> str:
    """Format category identifier as a PascalCase rule name."""
    cat_str = category.value if isinstance(category, IssueCategory) else str(category)
    parts = cat_str.replace("-", "_").split("_")
    pascal = "".join(p.capitalize() for p in parts if p)
    return pascal or cat_str


def _build_rule(rule_id: str, issue: AuditIssue) -> dict[str, Any]:
    """Construct a SARIF reportingDescriptor object for a rule."""
    desc = KNOWN_RULE_DESCRIPTIONS.get(rule_id, issue.title or f"Audit rule for {rule_id}.")
    return {
        "id": rule_id,
        "name": _to_rule_name(issue.category),
        "shortDescription": {
            "text": desc,
        },
        "defaultConfiguration": {
            "level": _severity_to_sarif_level(issue.severity),
        },
    }


def _build_result(issue: AuditIssue) -> dict[str, Any]:
    """Construct a SARIF result object for an audit issue."""
    rule_id = _to_rule_id(issue.category)
    level = _severity_to_sarif_level(issue.severity)

    if issue.title and issue.description:
        message_text = f"{issue.title}: {issue.description}"
    else:
        message_text = issue.description or issue.title or "Issue detected"

    raw_path = issue.file_path or "project.godot"
    clean_path = raw_path.replace("\\", "/")
    start_line = issue.line_number if issue.line_number and issue.line_number > 0 else 1

    return {
        "ruleId": rule_id,
        "level": level,
        "message": {
            "text": message_text,
        },
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": clean_path,
                        "uriBaseId": "%SRCROOT%",
                    },
                    "region": {
                        "startLine": start_line,
                    },
                }
            }
        ],
    }


def render_sarif(
    result: AuditResult,
    min_severity: Severity = Severity.LOW,
    output_path: Path | None = None,
) -> str:
    """Render audit findings in SARIF 2.1.0 format and optionally write to file.

    Compatible with GitHub Code Scanning (github/codeql-action/upload-sarif).
    """
    if isinstance(min_severity, str) and not isinstance(min_severity, Severity):
        min_severity = Severity.from_string(min_severity)

    filtered_issues = result.filter_by_severity(min_severity)

    # Sort issues deterministically: descending severity rank, then file path, then line number
    sorted_issues = sorted(
        filtered_issues,
        key=lambda x: (
            -x.severity.rank,
            x.file_path or "",
            x.line_number or 0,
        ),
    )

    # Build unique rules preserving order and selecting highest severity
    rules_map: dict[str, dict[str, Any]] = {}
    highest_ranks: dict[str, int] = {}

    for issue in sorted_issues:
        r_id = _to_rule_id(issue.category)
        if r_id not in rules_map:
            rules_map[r_id] = _build_rule(r_id, issue)
            highest_ranks[r_id] = issue.severity.rank
        elif issue.severity.rank > highest_ranks[r_id]:
            highest_ranks[r_id] = issue.severity.rank
            rules_map[r_id]["defaultConfiguration"]["level"] = _severity_to_sarif_level(
                issue.severity
            )

    sarif_rules = list(rules_map.values())
    sarif_results = [_build_result(issue) for issue in sorted_issues]

    sarif_doc: dict[str, Any] = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "gd-audit",
                        "semanticVersion": __version__,
                        "informationUri": INFORMATION_URI,
                        "rules": sarif_rules,
                    }
                },
                "results": sarif_results,
            }
        ],
    }

    formatted_sarif = json.dumps(sarif_doc, indent=2, ensure_ascii=False)

    if output_path is not None:
        target = Path(output_path)
        target.write_text(formatted_sarif, encoding="utf-8")

    return formatted_sarif
