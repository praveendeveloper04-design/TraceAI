"""
BaseInvestigationAgent — Abstract base for all investigation agents.

Each agent has a name, dependencies, timeout, and typed output.
Agents read dependency outputs from AgentContext and write their own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

from task_analyzer.investigation.agents.context import AgentContext

logger = structlog.get_logger(__name__)


class BaseInvestigationAgent(ABC):
    """
    Abstract base for investigation agents.

    Subclasses define:
      name: str           — unique agent identifier
      depends_on: list     — agent names this depends on
      priority: int        — lower = runs earlier in same wave
      timeout: float       — max execution time in seconds
    """

    name: str = "base"
    depends_on: list[str] = []
    priority: int = 5
    timeout: float = 30.0

    @abstractmethod
    async def execute(self, ctx: AgentContext, **kwargs: Any) -> Any:
        """Execute the agent's work. Returns typed output."""
        ...

    async def execute_safe(self, ctx: AgentContext, **kwargs: Any) -> Any | None:
        """Execute with error isolation. Returns None on failure."""
        try:
            return await self.execute(ctx, **kwargs)
        except Exception as exc:
            logger.warning(
                "agent_failed",
                agent=self.name,
                error=str(exc)[:200],
                error_type=type(exc).__name__,
            )
            return None
