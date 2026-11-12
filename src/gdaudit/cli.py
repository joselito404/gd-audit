"""Command-line interface for gd-audit."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from typer.core import TyperGroup

from gdaudit import __version__
from gdaudit.config import (
    filter_by_baseline,
    filter_issues,
    load_baseline,
    save_baseline,
)
from gdaudit.core.autoloads import AutoloadAnalyzer
from gdaudit.core.gdscript import GDScriptAnalyzer
from gdaudit.core.resources import ResourceAnalyzer
from gdaudit.models import AuditIssue, AuditResult, Severity, is_valid_fail_on
from gdaudit.reporters.console import render_console, render_github_annotations
from gdaudit.reporters.json_reporter import render_json
from gdaudit.reporters.markdown import render_markdown
from gdaudit.reporters.sarif import render_sarif
from gdaudit.utils.godot_project import GodotProject


class AuditGroup(TyperGroup):
    """Custom TyperGroup that routes default arguments and flags to 'scan'."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        if not args:
            args = ["scan"]
        elif args[0] not in self.commands and not args[0].startswith("-"):
            args = ["scan"] + args
        elif args[0].startswith("-") and args[0] not in ("--help", "-h", "--version", "-v"):
            args = ["scan"] + args
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=AuditGroup,
    name="gd-audit",
    help="Static architecture linter and dependency health analyzer for Godot projects.",
    no_args_is_help=False,
    add_completion=False,
)
err_console = Console(stderr=True)
SUPPORTED_FORMATS: tuple[str, ...] = ("rich", "json", "markdown", "github", "sarif")


def configure_utf8_output() -> None:
    """Make stdout/stderr write UTF-8 so reports with emojis never fail on legacy consoles.

    Windows consoles often use cp1252, which cannot encode the characters in the reports.
    Streams that cannot be reconfigured (already replaced or closed) are left untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def version_callback(value: bool) -> None:
    """Print the version of gd-audit and exit."""
    if value:
        typer.echo(f"gd-audit v{__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = None,
) -> None:
    """gd-audit: Godot architecture and dependency auditor."""
    pass


@app.command(name="version", help="Show gd-audit version.")
def version_cmd() -> None:
    """Show gd-audit version."""
    typer.echo(f"gd-audit v{__version__}")


@app.command(name="scan")
def scan(
    path: Annotated[
        Path,
        typer.Argument(
            help="Path to Godot project root (directory containing project.godot) or subfolder",
            exists=False,
        ),
    ] = Path("."),
    severity: Annotated[
        str,
        typer.Option(
            "--severity",
            "-s",
            help="Minimum severity to display: info, low, medium, high, critical",
        ),
    ] = "low",
    fail_on: Annotated[
        str,
        typer.Option(
            "--fail-on",
            help="Severity that triggers a non-zero exit code: "
            "info, low, medium, high, critical, never",
        ),
    ] = "high",
    format_type: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Report output format: rich, json, markdown, github, sarif",
        ),
    ] = "rich",
    ignore_paths: Annotated[
        str | None,
        typer.Option(
            "--ignore-paths",
            "-i",
            help="Comma-separated or space-separated list of paths or glob patterns to ignore",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write formatted output to specified file path",
        ),
    ] = None,
    check_orphans: Annotated[
        bool,
        typer.Option(
            "--check-orphans/--no-check-orphans",
            help="Audit project for unreferenced orphan assets",
        ),
    ] = True,
    include_addons: Annotated[
        bool,
        typer.Option(
            "--include-addons",
            help="Also analyze the root 'addons/' folder (ignored by default)",
        ),
    ] = False,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            "-b",
            help="Path to baseline file to record or filter existing issues",
        ),
    ] = None,
) -> None:
    """Scan a Godot project for broken resources, autoload cycles, and dead code."""
    configure_utf8_output()
    start_time = time.perf_counter()

    # 1. Early CLI argument validation (BEFORE discovering/scanning project)
    # Reject wrong --fail-on
    if not is_valid_fail_on(fail_on):
        err_console.print(
            f"[bold red]Invalid --fail-on:[/bold red] '{escape(fail_on)}'. "
            "Choose from info, low, medium, high, critical, never."
        )
        raise typer.Exit(code=2)

    # Reject wrong --severity
    try:
        min_sev = Severity.from_string(severity)
    except ValueError:
        err_console.print(
            f"[bold red]Invalid severity:[/bold red] '{escape(severity)}'. "
            "Choose from info, low, medium, high, critical."
        )
        raise typer.Exit(code=2) from None

    # Reject wrong --format
    fmt = format_type.strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        err_console.print(
            f"[bold red]Invalid format:[/bold red] '{escape(format_type)}'. "
            f"Choose from {', '.join(SUPPORTED_FORMATS)}. "
            f"(Choose from {', '.join(SUPPORTED_FORMATS[:4])}.)"
        )
        raise typer.Exit(code=2)

    # Parse ignore patterns
    patterns: list[str] = []
    if ignore_paths:
        for p in re.split(r"[,\s]+", ignore_paths.strip()):
            if p:
                patterns.append(p)

    # Initialize Godot Project
    try:
        project = GodotProject(path, ignore_patterns=patterns, include_addons=include_addons)
    except FileNotFoundError as err:
        err_console.print(f"[bold red]Error:[/bold red] {err}")
        raise typer.Exit(code=1) from None
    except Exception as err:
        err_console.print(f"[bold red]Unexpected error discovering project:[/bold red] {err}")
        raise typer.Exit(code=1) from None

    # Run Analyzers
    issues: list[AuditIssue] = []

    autoload_analyzer = AutoloadAnalyzer(project)
    issues.extend(autoload_analyzer.analyze())

    resource_analyzer = ResourceAnalyzer(project)
    issues.extend(resource_analyzer.analyze(check_orphans=check_orphans))

    gdscript_analyzer = GDScriptAnalyzer(project)
    issues.extend(gdscript_analyzer.analyze())

    # Filter issues using configuration rules (.gd-audit.toml)
    issues = filter_issues(issues, project.config)

    # Baseline handling: record if absent, filter if present
    if baseline is not None:
        if not baseline.exists():
            save_baseline(baseline, issues)
            err_console.print(
                f"[bold green]Baseline generated at '{baseline}':[/bold green] "
                f"{len(issues)} issues recorded."
            )
            issues = []
        else:
            baseline_tuples, baseline_fingerprints = load_baseline(baseline)
            issues = filter_by_baseline(issues, baseline_tuples, baseline_fingerprints)

    duration = time.perf_counter() - start_time

    # Build Result
    result = AuditResult(
        project_path=project.root,
        project_name=project.name,
        issues=issues,
        file_stats=project.stats,
        duration_seconds=duration,
    )

    # Output Presentation
    if fmt == "rich":
        render_console(result, min_severity=min_sev)
        if output:
            render_markdown(result, min_severity=min_sev, output_path=output)
    elif fmt == "json":
        json_str = render_json(result, min_severity=min_sev, output_path=output)
        if not output:
            typer.echo(json_str)
    elif fmt == "markdown":
        md_str = render_markdown(result, min_severity=min_sev, output_path=output)
        if not output:
            typer.echo(md_str)
    elif fmt == "github":
        ann_str = render_github_annotations(result, min_severity=min_sev)
        if ann_str:
            typer.echo(ann_str)
        if output:
            output.write_text(ann_str, encoding="utf-8")
    elif fmt == "sarif":
        sarif_str = render_sarif(result, min_severity=min_sev, output_path=output)
        if not output:
            typer.echo(sarif_str)

    # Evaluate Exit Code
    if result.should_fail(fail_on):
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
