"""EvidenceMergeAgent — Merges all agent outputs into unified evidence. Wave 3, depends on all."""

from __future__ import annotations
from typing import Any
import structlog
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, MergedEvidence

logger = structlog.get_logger(__name__)


class EvidenceMergeAgent(BaseInvestigationAgent):
    name = "evidence_merge"
    depends_on = ["repo_scan", "schema_discovery", "code_flow",
                  "ticket_context", "entity_extraction",
                  "code_deep_dive", "sql_intelligence"]
    priority = 1
    timeout = 10.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> MergedEvidence:
        m = MergedEvidence()

        if ctx.entity_extraction:
            m.entities = ctx.entity_extraction.entities

        if ctx.code_deep_dive:
            m.code_files = ctx.code_deep_dive.code_files_with_content
            m.code_flows = ctx.code_deep_dive.code_flows
            m.repo_search_results = ctx.code_deep_dive.repo_search_results
        elif ctx.repo_scan:
            m.code_files = ctx.repo_scan.code_files

        if ctx.schema_discovery:
            m.sql_tables = ctx.schema_discovery.sql_tables
            m.sql_schema = ctx.schema_discovery.sql_schema
            m.tenant_db = ctx.schema_discovery.tenant_db

        if ctx.code_flow:
            m.layer_map = ctx.code_flow.layer_map

        if ctx.sql_intelligence:
            m.query_results = ctx.sql_intelligence.query_results
            m.relationships = ctx.sql_intelligence.relationships

        if ctx.ticket_context:
            m.ticket_context = {
                "related_tasks": ctx.ticket_context.related_tasks,
                "key_entities": ctx.ticket_context.key_entities,
            }

        # Quality assessment
        score = 0
        details = []
        fc = len(m.code_files)
        if fc > 0:
            score += min(30, fc * 3)
            details.append(f"{fc} code files")
        cf = len(m.code_flows)
        if cf > 0:
            score += min(20, cf * 5)
            details.append(f"{cf} code flows")
        st = len(m.sql_tables)
        if st > 0:
            score += min(15, st * 2)
            details.append(f"{st} SQL tables")
        sd = sum(1 for t in m.sql_tables if (t.get("row_count") or 0) > 0)
        if sd > 0:
            score += min(10, sd * 3)
            details.append(f"{sd} tables with data")
        qr = len(m.query_results)
        if qr > 0:
            score += min(15, qr * 3)
            details.append(f"{qr} query results")
        sr = len(m.repo_search_results)
        if sr > 0:
            score += min(10, sr * 2)
            details.append(f"{sr} code refs")

        level = "insufficient" if score < 30 else "partial" if score < 60 else "good" if score < 80 else "excellent"
        m.quality = {"score": min(score, 100), "level": level, "details": details}

        logger.info("evidence_merged", code_files=fc, code_flows=cf, sql_tables=st,
                     query_results=qr, quality=level)
        return m
