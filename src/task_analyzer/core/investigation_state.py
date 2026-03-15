"""
Investigation State Manager -- Tracks running investigations in memory.

Provides:
  - Registration of running investigations with asyncio tasks
  - Status polling with step/progress/logs
  - Cancellation of running investigations
  - Automatic cleanup when investigations complete
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class InvestigationState:
    """State of a single investigation."""

    def __init__(self, investigation_id: str, task_id: str, task_title: str = "") -> None:
        self.id = investigation_id
        self.task_id = task_id
        self.task_title = task_title
        self.status = "running"
        self.step = "initializing"
        self.progress = 0
        self.logs: list[dict[str, str]] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self._task: asyncio.Task | None = None

    def log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logs.append({"time": ts, "message": message})
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

    def set_step(self, step: str, progress: int) -> None:
        self.step = step
        self.progress = min(progress, 100)
        self.log(f"{step}")

    def complete(self, status: str = "completed") -> None:
        self.status = status
        self.progress = 100
        self.step = "done"
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "logs": self.logs[-20:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class InvestigationRegistry:
    """In-memory registry of running and recently completed investigations."""

    def __init__(self) -> None:
        self._states: dict[str, InvestigationState] = {}

    def register(self, state: InvestigationState) -> None:
        self._states[state.id] = state
        logger.info("investigation_registered", id=state.id, task_id=state.task_id)

    def get(self, investigation_id: str) -> InvestigationState | None:
        return self._states.get(investigation_id)

    def set_task(self, investigation_id: str, task: asyncio.Task) -> None:
        state = self._states.get(investigation_id)
        if state:
            state._task = task

    def cancel(self, investigation_id: str) -> bool:
        state = self._states.get(investigation_id)
        if not state:
            return False
        if state._task and not state._task.done():
            state._task.cancel()
            state.complete("cancelled")
            state.log("Investigation cancelled by user")
            logger.info("investigation_cancelled", id=investigation_id)
            return True
        return False

    def list_running(self) -> list[dict[str, Any]]:
        return [
            s.to_dict() for s in self._states.values()
            if s.status == "running"
        ]

    def list_all(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._states.values()]

    def cleanup_old(self, max_age_seconds: int = 3600) -> None:
        """Remove completed states older than max_age_seconds."""
        now = datetime.now(timezone.utc)
        to_remove = []
        for sid, state in self._states.items():
            if state.status != "running" and state.finished_at:
                try:
                    finished = datetime.fromisoformat(state.finished_at)
                    if (now - finished).total_seconds() > max_age_seconds:
                        to_remove.append(sid)
                except Exception:
                    pass
        for sid in to_remove:
            del self._states[sid]


# Module-level singleton
investigation_registry = InvestigationRegistry()
