"""
Base Skill — Abstract interface for investigation skills.

Skills are reusable investigation workflows that combine multiple
tools and connectors to analyze a specific type of issue.
Each skill declares its required tools and runs through the
SecurityGuard — skills cannot bypass security validation.

Community contributors can add new skills by subclassing BaseSkill
and registering them with the SkillRegistry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from task_analyzer.core.security_guard import SecurityGuard
    from task_analyzer.investigation.graph_engine import InvestigationGraph
    from task_analyzer.models.schemas import Task


class BaseSkill(ABC):
    """
    Base interface for investigation skills.

    Skills are reusable investigation workflows that combine multiple
    tools and connectors to analyze a specific type of issue.
    Each skill declares its required tools and runs through the
    SecurityGuard — skills cannot bypass security validation.

    Community contributors can add new skills by subclassing BaseSkill
    and registering them with the SkillRegistry.
    """

    name: str                          # e.g. "repo_analysis"
    display_name: str                  # e.g. "Repository Analysis"
    description: str                   # What this skill does
    required_tools: list[str]          # Tool names from TOOL_REGISTRY

    @abstractmethod
    async def run(
        self,
        task: Task,
        context: dict[str, Any],
        security_guard: SecurityGuard,
        connectors: dict,
        graph: InvestigationGraph,
    ) -> dict[str, Any]:
        """
        Execute the skill's investigation workflow.

        Args:
            task: The Task being investigated
            context: Shared investigation context dict
            security_guard: SecurityGuard instance (must validate all ops)
            connectors: Dict of active connector instances
            graph: InvestigationGraph to record discovered relationships

        Returns:
            Dict with skill-specific findings to merge into the report
        """
        ...

    def is_available(self, connectors: dict) -> bool:
        """
        Check if required connectors are configured.

        Override in subclasses that depend on specific connector types.
        Default: always available.
        """
        return True
