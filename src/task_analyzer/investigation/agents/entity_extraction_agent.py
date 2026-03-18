"""EntityExtractionAgent — Extracts entities and action keywords. Wave 1, no deps, <1s."""

from __future__ import annotations
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, EntityExtractionOutput


class EntityExtractionAgent(BaseInvestigationAgent):
    name = "entity_extraction"
    depends_on = []
    priority = 0
    timeout = 5.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> EntityExtractionOutput:
        from task_analyzer.investigation.planner import EntityExtractor
        ext = EntityExtractor()
        entities = ext.extract(kwargs["task_title"], kwargs.get("task_description", ""))

        # Extract action keywords for targeted search
        action_keywords = []
        text = f"{kwargs['task_title']} {kwargs.get('task_description', '')}"
        try:
            from task_analyzer.investigation.deep_investigator import DeepInvestigator
            di = DeepInvestigator([], {})
            di.evidence["entities"] = entities
            action_keywords = di._extract_action_keywords(text)
        except Exception:
            pass

        return EntityExtractionOutput(entities=entities, action_keywords=action_keywords)
