"""
Skill Registry — Central registry for investigation skills.

Skills are discovered and executed by the investigation engine.
The registry manages skill lifecycle and availability checking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class SkillRegistry:
    """
    Central registry for investigation skills.
    Skills are discovered and executed by the investigation engine.
    """

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill instance."""
        self._skills[skill.name] = skill
        logger.debug("skill_registered", name=skill.name, display=skill.display_name)

    def get(self, name: str) -> BaseSkill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_available(self, connectors: dict) -> list[BaseSkill]:
        """Return skills that can run with current connectors."""
        available = []
        for skill in self._skills.values():
            try:
                if skill.is_available(connectors):
                    available.append(skill)
            except Exception as exc:
                logger.warning(
                    "skill_availability_check_failed",
                    skill=skill.name,
                    error=str(exc),
                )
        return available

    def list_all(self) -> list[dict[str, str]]:
        """Return metadata for all registered skills."""
        return [
            {
                "name": s.name,
                "display_name": s.display_name,
                "description": s.description,
            }
            for s in self._skills.values()
        ]
