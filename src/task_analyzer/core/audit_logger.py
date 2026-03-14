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
