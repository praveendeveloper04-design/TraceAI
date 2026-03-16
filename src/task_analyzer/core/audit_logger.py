"""
Audit Logger — Logs every investigation action for transparency.

Location: ~/.traceai/logs/investigation.log

Each entry records: timestamp, event type, ticket_id, tool_used,
operation, status, duration_ms.

All entries are structured JSON, one per line (JSONL format).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class AuditLogger:
    """
    Logs every investigation action for transparency.
    Location: ~/.traceai/logs/investigation.log

    Each entry records: timestamp, ticket_id, tool_used,
    operation, status, duration_ms
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or Path.home() / ".traceai" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "investigation.log"

    def log(self, event: str, **kwargs) -> None:
        """Append a structured log entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **kwargs,
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:
            logger.warning("audit_log_write_failed", error=str(exc))

    def log_investigation_start(self, task_id: str, task_title: str) -> None:
        """Log the start of an investigation."""
        self.log(
            "investigation_start",
            task_id=task_id,
            task_title=task_title,
        )

    def log_tool_call(
        self,
        task_id: str,
        tool_name: str,
        operation: str,
        status: str,
        duration_ms: int = 0,
    ) -> None:
        """Log a tool call during investigation."""
        self.log(
            "tool_call",
            task_id=task_id,
            tool=tool_name,
            operation=operation,
            status=status,
            duration_ms=duration_ms,
        )

    def log_skill_execution(
        self,
        task_id: str,
        skill_name: str,
        status: str,
        duration_ms: int = 0,
        findings_count: int = 0,
    ) -> None:
        """Log a skill execution during investigation."""
        self.log(
            "skill_execution",
            task_id=task_id,
            skill=skill_name,
            status=status,
            duration_ms=duration_ms,
            findings_count=findings_count,
        )

    def log_security_violation(
        self,
        task_id: str,
        tool_name: str,
        operation: str,
        reason: str,
    ) -> None:
        """Log a security violation attempt."""
        self.log(
            "security_violation",
            task_id=task_id,
            tool=tool_name,
            operation=operation,
            reason=reason,
        )

    def log_investigation_complete(
        self,
        task_id: str,
        status: str,
        findings_count: int,
        duration_ms: int,
    ) -> None:
        """Log the completion of an investigation."""
        self.log(
            "investigation_complete",
            task_id=task_id,
            status=status,
            findings=findings_count,
            duration_ms=duration_ms,
        )

    # ── Investigation Telemetry ──────────────────────────────────────────

    def log_workspace_loaded(
        self,
        task_id: str,
        repositories: list[str],
        dependencies: dict[str, list[str]],
        services: list[str],
        index_stats: dict | None = None,
    ) -> None:
        """Log workspace architecture loaded for investigation."""
        self.log(
            "workspace_loaded",
            task_id=task_id,
            repositories=repositories,
            dependencies=dependencies,
            services=services,
            index_stats=index_stats,
        )

    def log_task_classified(
        self,
        task_id: str,
        category: str,
        strategy: str,
        complexity: str,
        confidence: float,
        signals: list[str],
    ) -> None:
        """Log task classification decision."""
        self.log(
            "task_classified",
            task_id=task_id,
            category=category,
            strategy=strategy,
            complexity=complexity,
            confidence=confidence,
            signals=signals,
        )

    def log_tables_ranked(
        self,
        task_id: str,
        tables: list[dict],
        total_schema_tables: int,
    ) -> None:
        """Log ranked SQL table selection decision."""
        self.log(
            "tables_ranked",
            task_id=task_id,
            selected_tables=tables,
            total_schema_tables=total_schema_tables,
        )

    def log_skills_selected(
        self,
        task_id: str,
        selected: list[str],
        skipped: list[str],
        reason: str,
    ) -> None:
        """Log dynamic skill orchestration decision."""
        self.log(
            "skills_selected",
            task_id=task_id,
            selected=selected,
            skipped=skipped,
            reason=reason,
        )

    def log_dependency_resolution(
        self,
        task_id: str,
        primary_repo: str,
        loaded_repos: list[str],
        profiles_count: int,
    ) -> None:
        """Log repository dependency resolution."""
        self.log(
            "dependency_resolution",
            task_id=task_id,
            primary_repo=primary_repo,
            loaded_repos=loaded_repos,
            profiles_count=profiles_count,
        )

    def log_deep_investigation_decision(
        self,
        task_id: str,
        loops_completed: int,
        confidence: float,
        early_stop: bool,
        evidence_quality: str,
    ) -> None:
        """Log deep investigation loop decision."""
        self.log(
            "deep_investigation_decision",
            task_id=task_id,
            loops_completed=loops_completed,
            confidence=confidence,
            early_stop=early_stop,
            evidence_quality=evidence_quality,
        )
