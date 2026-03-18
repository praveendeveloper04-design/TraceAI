"""RepoScanAgent — Scans repos for entity-matching files. Wave 1, no deps."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, RepoScanOutput


class RepoScanAgent(BaseInvestigationAgent):
    name = "repo_scan"
    depends_on = []
    priority = 1
    timeout = 20.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> RepoScanOutput:
        profiles = kwargs["profiles"]
        entities = kwargs["entities"]

        repo_paths = [Path(getattr(p, "repo_path", "")) for p in profiles
                      if getattr(p, "repo_path", None) and Path(getattr(p, "repo_path", "")).exists()]

        if not repo_paths or not entities:
            return RepoScanOutput()

        from task_analyzer.investigation.deep_investigator import DeepInvestigator
        di = DeepInvestigator(profiles=profiles, connectors={})
        di._search_repos_broad(repo_paths, entities)

        cb = kwargs.get("progress_callback")
        if cb:
            await cb("repo_scan", f"Found {len(di.evidence['code_files'])} files in {len(repo_paths)} repos")

        return RepoScanOutput(
            code_files=di.evidence["code_files"],
            file_count=len(di.evidence["code_files"]),
            repos_scanned=len(repo_paths),
        )
