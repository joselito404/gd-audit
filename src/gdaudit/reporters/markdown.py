"""Markdown reporter designed for GitHub Actions Step Summaries ($GITHUB_STEP_SUMMARY)."""

from __future__ import annotations

from pathlib import Path

from gdaudit.models import AuditResult, Severity

SEVERITY_BADGES = {
    Severity.CRITICAL: "🔴 **CRITICAL**",
    Severity.HIGH: "🔴 **HIGH**",
    Severity.MEDIUM: "🟡 **MEDIUM**",
    Severity.LOW: "🔵 **LOW**",
    Severity.INFO: "ℹ️ **INFO**",
}


def render_markdown(
    result: AuditResult,
    min_severity: Severity = Severity.LOW,
    output_path: Path | None = None,
) -> str:
    """Render audit findings as formatted Markdown."""
    counts = result.counts_by_severity()
    stats = result.file_stats
    filtered_issues = result.filter_by_severity(min_severity)

    # Sort issues
    sorted_issues = sorted(
        filtered_issues,
        key=lambda x: (
            -x.severity.rank,
            x.file_path or "",
            x.line_number or 0,
        ),
    )

    high_crit = counts["high"] + counts["critical"]
    summary_row = (
        f"| **{len(result.issues)}** | {high_crit} | "
        f"{counts['medium']} | {counts['low']} | {counts['info']} |"
    )

    lines: list[str] = [
        "# 🛡️ gd-audit Report",
        "",
        f"**Project:** `{result.project_name}` ({result.project_path.resolve()})  ",
        f"**Duration:** {result.duration_seconds:.3f}s  ",
        f"**Files Scanned:** {stats.get('gd_scripts', 0)} scripts, "
        f"{stats.get('scenes', 0)} scenes, {stats.get('resources', 0)} resources, "
        f"{stats.get('assets', 0)} assets",
        "",
        "### 📊 Summary",
        "",
        "| Total | 🔴 High | 🟡 Medium | 🔵 Low | ℹ️ Info |",
        "| :---: | :---: | :---: | :---: | :---: |",
        summary_row,
        "",
    ]

    if not sorted_issues:
        lines.append("🎉 **No issues detected! Your project architecture is clean.**\n")
    else:
        lines.extend(
            [
                f"### 🔍 Detailed Findings ({len(sorted_issues)} issues)",
                "",
                "| Severity | Category | Location | Description |",
                "| :---: | :--- | :--- | :--- |",
            ]
        )

        for issue in sorted_issues:
            badge = SEVERITY_BADGES.get(issue.severity, str(issue.severity.value))
            category_str = (
                issue.category.value if hasattr(issue.category, "value") else str(issue.category)
            )

            location = f"`{issue.file_path}`" if issue.file_path else "—"
            if issue.line_number is not None and issue.file_path:
                location = f"`{issue.file_path}:{issue.line_number}`"

            # Clean newlines for markdown table compatibility
            desc = issue.description.replace("\n", " ")
            if issue.context:
                desc += f" <br><sub>*Context: `{issue.context}`*</sub>"

            lines.append(f"| {badge} | `{category_str}` | {location} | {desc} |")

        lines.append("")

    content = "\n".join(lines)

    if output_path:
        output_path.write_text(content, encoding="utf-8")

    return content
