"""TicketContextAgent — Fetches related tickets from Azure DevOps. Wave 1, no deps."""

from __future__ import annotations
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, TicketContextOutput


class TicketContextAgent(BaseInvestigationAgent):
    name = "ticket_context"
    depends_on = []
    priority = 5
    timeout = 60.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> TicketContextOutput:
        task = kwargs.get("_task_obj")
        if not task:
            return TicketContextOutput()

        connectors = kwargs["connectors"]
        profiles = kwargs["profiles"]

        from task_analyzer.skills.ticket_context import TicketContextSkill
        from task_analyzer.core.security_guard import SecurityGuard
        from task_analyzer.investigation.graph_engine import InvestigationGraph

        skill = TicketContextSkill()
        if not skill.is_available(connectors):
            return TicketContextOutput()

        guard = SecurityGuard(safe_mode=True)
        graph = InvestigationGraph()
        graph.add_node(task.id, "ticket", {"title": task.title})

        result = await skill.run(task, {"profiles": profiles}, guard, connectors, graph)

        return TicketContextOutput(
            related_tasks=result.get("related_tasks", []),
            key_entities=result.get("key_entities", []),
            timeline=result.get("timeline", []),
        )
