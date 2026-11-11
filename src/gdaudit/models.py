"""Data models and type definitions for gd-audit."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    """Severity levels for audit findings, ordered by priority."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Numeric rank for comparing severity levels."""
        ranks = {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4,
        }
        return ranks[self]

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank >= other.rank
        if isinstance(other, str):
            parsed = Severity.from_string(other)
            return self.rank >= parsed.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank > other.rank
        if isinstance(other, str):
            parsed = Severity.from_string(other)
            return self.rank > parsed.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank <= other.rank
        if isinstance(other, str):
            parsed = Severity.from_string(other)
            return self.rank <= parsed.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank < other.rank
        if isinstance(other, str):
            parsed = Severity.from_string(other)
            return self.rank < parsed.rank
        return NotImplemented

    @classmethod
    def from_string(cls, value: str) -> Severity:
        """Normalize string representations to a Severity enum."""
        normalized = value.strip().lower()
        mapping = {
            "info": cls.INFO,
            "information": cls.INFO,
            "low": cls.LOW,
            "warn": cls.MEDIUM,
            "warning": cls.MEDIUM,
            "medium": cls.MEDIUM,
            "high": cls.HIGH,
            "error": cls.HIGH,
            "crit": cls.CRITICAL,
            "critical": cls.CRITICAL,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise ValueError(f"Invalid severity level: {value}")


class IssueCategory(str, Enum):
    """Categorization tags for detected issues."""

    AUTOLOAD_CYCLE = "autoload_cycle"
    UNUSED_AUTOLOAD = "unused_autoload"
    BROKEN_RESOURCE = "broken_resource"
    UNUSED_RESOURCE = "unused_resource"
    ORPHAN_ASSET = "orphan_asset"
    DEAD_FUNCTION = "dead_function"
    PERFORMANCE_ANTIPATTERN = "performance_antipattern"


@dataclass(frozen=True)
class AuditIssue:
    """Represents an issue found during project audit."""

    category: IssueCategory | str
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert issue to a JSON-serializable dictionary."""
        data = asdict(self)
        data["category"] = (
            self.category.value if isinstance(self.category, IssueCategory) else str(self.category)
        )
        data["severity"] = self.severity.value
        return data


@dataclass
class AuditResult:
    """Aggregated audit results across an entire Godot project."""

    project_path: Path
    project_name: str
    issues: list[AuditIssue] = field(default_factory=list)
    file_stats: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def filter_by_severity(self, min_severity: Severity) -> list[AuditIssue]:
        """Return only issues meeting or exceeding the minimum severity threshold."""
        return [issue for issue in self.issues if issue.severity >= min_severity]

    def counts_by_severity(self) -> dict[str, int]:
        """Count issues grouped by severity."""
        counts = {
            Severity.CRITICAL.value: 0,
            Severity.HIGH.value: 0,
            Severity.MEDIUM.value: 0,
            Severity.LOW.value: 0,
            Severity.INFO.value: 0,
        }
        for issue in self.issues:
            counts[issue.severity.value] += 1
        return counts

    def should_fail(self, fail_on: str) -> bool:
        """Determine if issues trigger a non-zero exit code based on the fail-on threshold."""
        normalized = fail_on.strip().lower()
        if normalized in ("never", "none", "off"):
            return False

        threshold = Severity.from_string(normalized)
        return any(issue.severity >= threshold for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a structured dictionary."""
        try:
            rel_path = self.project_path.resolve().relative_to(Path.cwd().resolve())
            proj_path_str = str(rel_path) if str(rel_path) != "." else self.project_name
        except (ValueError, RuntimeError):
            proj_path_str = self.project_name

        return {
            "project": {
                "name": self.project_name,
                "path": proj_path_str,
            },
            "summary": {
                "total_issues": len(self.issues),
                "counts": self.counts_by_severity(),
                "duration_seconds": round(self.duration_seconds, 4),
                "files_scanned": self.file_stats,
            },
            "issues": [issue.to_dict() for issue in self.issues],
        }
