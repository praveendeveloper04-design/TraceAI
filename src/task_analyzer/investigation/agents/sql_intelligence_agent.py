"""SQLIntelligenceAgent — Generates and executes smart queries. Wave 2, depends on schema_discovery."""

from __future__ import annotations
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, SQLIntelligenceOutput


class SQLIntelligenceAgent(BaseInvestigationAgent):
    name = "sql_intelligence"
    depends_on = ["schema_discovery"]
    priority = 2
    timeout = 30.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> SQLIntelligenceOutput:
        connectors = kwargs["connectors"]
        plan = kwargs.get("plan")
        classification = kwargs.get("classification")
        entities = kwargs["entities"]

        schema_out = ctx.schema_discovery
        code_flow_out = ctx.code_flow

        if not schema_out or not schema_out.schema_info:
            return SQLIntelligenceOutput()

        tenant_db = schema_out.tenant_db
        if not tenant_db:
            return SQLIntelligenceOutput()

        db_connector = None
        for _, conn in connectors.items():
            ct = getattr(conn, "connector_type", None)
            if ct and hasattr(ct, "value") and ct.value == "sql_database":
                db_connector = conn
                break
        if not db_connector:
            return SQLIntelligenceOutput()

        from task_analyzer.investigation.sql_intelligence import SQLIntelligence
        sql_eng = SQLIntelligence(tenant_db=tenant_db)

        tables = [t.get("name", t.get("table", "")) for t in schema_out.sql_tables]
        if plan and hasattr(plan, "tables"):
            for t in plan.tables:
                if t not in tables:
                    tables.append(t)

        code_tables = code_flow_out.db_tables_referenced if code_flow_out else []
        task_category = classification.category if classification else "unknown"

        queries = sql_eng.generate_queries(
            tables=tables, schema_info=schema_out.schema_info,
            task_category=task_category, entities=entities, code_tables=code_tables,
        )
        relationships = sql_eng.discover_relationships(db_connector, tables)
        query_results = sql_eng.execute_queries(db_connector, queries, max_queries=12)

        return SQLIntelligenceOutput(
            queries_generated=len(queries),
            queries_executed=len(query_results),
            query_results=query_results,
            relationships=[r.to_dict() for r in relationships],
        )
