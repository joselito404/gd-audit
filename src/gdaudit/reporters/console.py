"""Console reporter utilizing Rich tables, panels, and semantic colors."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gdaudit.models import AuditResult, Severity

SEVERITY_STYLES = {
    Severity.CRITICAL: "bold white on red",
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "bold yellow",
    Severity.LOW: "bold cyan",
    Severity.INFO: "bold blue",
}

SEVERITY_LABELS = {
    Severity.CRITICAL: "CRITICAL",
    Severity.HIGH: "HIGH",
    Severity.MEDIUM: "MEDIUM",
    Severity.LOW: "LOW",
    Severity.INFO: "INFO",
}


def render_console(
    result: AuditResult,
    min_severity: Severity = Severity.LOW,
    console: Console | None = None,
) -> None:
    """Render structured audit results to the terminal with Rich."""
    c = console or Console()
    filtered_issues = result.filter_by_severity(min_severity)

    # Sort issues: descending severity rank, then file path, then line number
    sorted_issues = sorted(
        filtered_issues,
        key=lambda x: (
            -x.severity.rank,
            x.file_path or "",
            x.line_number or 0,
        ),
    )

    c.print()
    if not sorted_issues:
        c.print(
            Panel(
                Text("✓ All checks passed! No issues detected.", style="bold green"),
                title="gd-audit",
                border_style="green",
            )
        )
    else:
        table = Table(
            title=f"🛡️ gd-audit Findings ({len(sorted_issues)} issues)",
            box=box.ROUNDED,
            header_style="bold magenta",
            show_lines=True,
        )

        table.add_column("Severity", justify="center", width=10)
        table.add_column("Category", style="cyan", width=22)
        table.add_column("Location", style="dim", width=30)
        table.add_column("Description", style="white")

        for issue in sorted_issues:
            sev_style = SEVERITY_STYLES.get(issue.severity, "white")
            sev_label = SEVERITY_LABELS.get(issue.severity, issue.severity.value.upper())

            location = issue.file_path or "-"
            if issue.line_number is not None:
                location = f"{location}:{issue.line_number}"

            desc_text = Text()
            desc_text.append(issue.title + "\n", style="bold")
            desc_text.append(issue.description, style="dim white")
            if issue.context:
                desc_text.append(f"\nContext: {issue.context}", style="italic dim yellow")

            table.add_row(
                Text(sev_label, style=sev_style),
                issue.category.value if hasattr(issue.category, "value") else str(issue.category),
                location,
                desc_text,
            )

        c.print(table)

    # Summary Panel
    counts = result.counts_by_severity()
    stats = result.file_stats

    summary_text = Text()
    summary_text.append("Project: ", style="bold")
    summary_text.append(f"{result.project_name} ({result.project_path.resolve()})\n")
    summary_text.append("Files Scanned: ", style="bold")
    summary_text.append(
        f"{stats.get('gd_scripts', 0)} scripts, "
        f"{stats.get('scenes', 0)} scenes, "
        f"{stats.get('resources', 0)} resources, "
        f"{stats.get('assets', 0)} assets\n"
    )
    summary_text.append("Issues: ", style="bold")
    summary_text.append(f"Total: {len(result.issues)} | ")
    summary_text.append(f"High: {counts['high']}  ", style="bold red")
    summary_text.append(f"Medium: {counts['medium']}  ", style="bold yellow")
    summary_text.append(f"Low: {counts['low']}  ", style="bold cyan")
    summary_text.append(f"Info: {counts['info']}\n", style="bold blue")
    summary_text.append("Duration: ", style="bold")
    summary_text.append(f"{result.duration_seconds:.3f}s\n")

    border_color = (
        "red"
        if (counts["critical"] + counts["high"]) > 0
        else "yellow"
        if counts["medium"] > 0
        else "green"
    )
    c.print(
        Panel(
            summary_text,
            title="Audit Summary",
            border_style=border_color,
            box=box.ROUNDED,
        )
    )


def render_github_annotations(
    result: AuditResult,
    min_severity: Severity = Severity.LOW,
) -> str:
    """Format audit issues as GitHub Actions workflow commands (::error / ::warning)."""
    lines: list[str] = []
    filtered = result.filter_by_severity(min_severity)

    for issue in filtered:
        level = "error" if issue.severity in (Severity.CRITICAL, Severity.HIGH) else "warning"
        file_part = f"file={issue.file_path}" if issue.file_path else "file=project.godot"
        line_part = f",line={issue.line_number}" if issue.line_number else ""
        title_part = f",title={issue.title}"

        # Clean newlines from description for workflow command format
        clean_desc = issue.description.replace("\n", " ").replace("\r", "")
        lines.append(f"::{level} {file_part}{line_part}{title_part}::{clean_desc}")

    return "\n".join(lines)
