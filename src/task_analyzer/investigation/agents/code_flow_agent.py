"""CodeFlowAgent — Traces Controller->Service->Repository->DB layers. Wave 1, no deps."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, CodeFlowOutput


class CodeFlowAgent(BaseInvestigationAgent):
    name = "code_flow"
    depends_on = []
    priority = 2
    timeout = 30.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> CodeFlowOutput:
        entities = kwargs["entities"]
        profiles = kwargs["profiles"]

        repo_paths = [Path(getattr(p, "repo_path", "")) for p in profiles
                      if getattr(p, "repo_path", None) and Path(getattr(p, "repo_path", "")).exists()]

        if not repo_paths or not entities:
            return CodeFlowOutput()

        from task_analyzer.investigation.code_flow_engine import CodeFlowAnalysisEngine
        engine = CodeFlowAnalysisEngine()
        layer_map = engine.analyze(entities, repo_paths)

        return CodeFlowOutput(
            layer_map=layer_map,
            db_tables_referenced=layer_map.db_tables_referenced if layer_map else [],
        )
