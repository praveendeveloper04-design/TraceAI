"""CodeDeepDiveAgent — Reads files, targeted search, code tables. Wave 2, depends on repo_scan + entity_extraction."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, CodeDeepDiveOutput


class CodeDeepDiveAgent(BaseInvestigationAgent):
    name = "code_deep_dive"
    depends_on = ["repo_scan", "entity_extraction"]
    priority = 1
    timeout = 30.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> CodeDeepDiveOutput:
        profiles = kwargs["profiles"]
        entities = kwargs["entities"]
        connectors = kwargs["connectors"]
        plan = kwargs.get("plan")

        repo_scan = ctx.repo_scan
        entity_out = ctx.entity_extraction

        code_files = repo_scan.code_files if repo_scan else []
        action_keywords = entity_out.action_keywords if entity_out else []

        repo_paths = [Path(getattr(p, "repo_path", "")) for p in profiles
                      if getattr(p, "repo_path", None) and Path(getattr(p, "repo_path", "")).exists()]

        from task_analyzer.investigation.deep_investigator import DeepInvestigator
        di = DeepInvestigator(profiles=profiles, connectors=connectors, plan=plan)
        di.evidence["code_files"] = list(code_files)
        di.evidence["entities"] = list(entities)

        # Loop 2a: Read and analyze files
        if code_files:
            di._read_and_analyze_files(entities)

        # Loop 2b: Targeted search
        if repo_paths and action_keywords:
            di._search_repos_targeted(repo_paths, action_keywords)

        # Loop 2c: Extract table names from code
        code_tables = di._extract_tables_from_found_code()

        # Loop 2d: Query code-discovered tables if schema available
        schema_out = ctx.schema_discovery
        if schema_out and schema_out.tenant_db and code_tables:
            db_connector = None
            for _, conn in connectors.items():
                ct = getattr(conn, "connector_type", None)
                if ct and hasattr(ct, "value") and ct.value == "sql_database":
                    db_connector = conn
                    break
            if db_connector:
                di._query_code_discovered_tables(db_connector, schema_out.tenant_db, code_tables)

        # Loop 3: Verification if evidence is thin
        confidence = di._confidence_score()
        if confidence < 0.5 and repo_paths:
            text = f"{kwargs['task_title']} {kwargs.get('task_description', '')}"
            di._search_repos_deep(repo_paths, entities, text)

        cb = kwargs.get("progress_callback")
        if cb:
            await cb("code_deep_dive", f"Analyzed {len(di.evidence['code_files'])} files, {len(di.evidence['code_flows'])} flows")

        return CodeDeepDiveOutput(
            code_files_with_content=[f for f in di.evidence["code_files"] if f.get("content")],
            code_flows=di.evidence["code_flows"],
            repo_search_results=di.evidence["repo_search_results"],
            code_discovered_tables=code_tables,
        )
