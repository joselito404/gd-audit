"""JSON reporter for gd-audit results."""

from __future__ import annotations

import json
from pathlib import Path

from gdaudit.models import AuditResult, Severity


def render_json(
    result: AuditResult,
    min_severity: Severity = Severity.INFO,
    output_path: Path | None = None,
) -> str:
    """Serialize audit results into structured JSON string and optionally write to file."""
    data = result.to_dict()

    # Filter issues by minimum requested severity and sort deterministically
    filtered_issues = [
        issue for issue in data["issues"] if Severity.from_string(issue["severity"]) >= min_severity
    ]
    filtered_issues.sort(
        key=lambda x: (
            -Severity.from_string(x["severity"]).rank,
            x.get("file_path") or "",
            x.get("line_number") or 0,
        )
    )
    data["issues"] = filtered_issues
    data["summary"]["reported_issues"] = len(filtered_issues)

    formatted_json = json.dumps(data, indent=2, ensure_ascii=False)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(formatted_json, encoding="utf-8")

    return formatted_json
