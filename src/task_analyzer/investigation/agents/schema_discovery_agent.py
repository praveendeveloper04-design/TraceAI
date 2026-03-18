"""SchemaDiscoveryAgent — Discovers DB schema and queries tables. Wave 1, no deps."""

from __future__ import annotations
from typing import Any
from task_analyzer.investigation.agents.base_agent import BaseInvestigationAgent
from task_analyzer.investigation.agents.context import AgentContext, SchemaDiscoveryOutput


class SchemaDiscoveryAgent(BaseInvestigationAgent):
    name = "schema_discovery"
    depends_on = []
    priority = 1
    timeout = 25.0

    async def execute(self, ctx: AgentContext, **kwargs: Any) -> SchemaDiscoveryOutput:
        connectors = kwargs["connectors"]
        plan = kwargs.get("plan")
        entities = kwargs["entities"]
        profiles = kwargs["profiles"]

        tenant_db = plan.tenant_db if plan and hasattr(plan, "tenant_db") else None

        db_connector = None
        for _, conn in connectors.items():
            ct = getattr(conn, "connector_type", None)
            if ct and hasattr(ct, "value") and ct.value == "sql_database":
                db_connector = conn
                break

        if not db_connector or not tenant_db:
            return SchemaDiscoveryOutput()

        from task_analyzer.investigation.deep_investigator import DeepInvestigator
        di = DeepInvestigator(profiles=profiles, connectors=connectors, plan=plan)
        di._discover_schema(db_connector, tenant_db, entities)
        di._query_tables(db_connector, tenant_db)

        schema_info: dict[str, list[dict]] = {}
        for s in di.evidence["sql_schema"]:
            schema_info[s["table"]] = s.get("columns", [])

        cb = kwargs.get("progress_callback")
        if cb:
            await cb("schema_discovery", f"Discovered {len(di.evidence['sql_tables'])} tables in {tenant_db}")

        return SchemaDiscoveryOutput(
            sql_tables=di.evidence["sql_tables"],
            sql_schema=di.evidence["sql_schema"],
            schema_info=schema_info,
            tenant_db=tenant_db,
            tables_with_data=sum(1 for t in di.evidence["sql_tables"] if (t.get("row_count") or 0) > 0),
        )
