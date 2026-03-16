"""
SQLQuerySkill -- Executes planned read-only SQL queries during investigation.

Unlike DatabaseAnalysisSkill which guesses table names from task keywords,
this skill executes specific queries determined by the InvestigationPlanner.
The planner identifies relevant tables from the system map, and this skill
fetches recent rows from those tables.

Security: ALL queries are validated through SecurityGuard before execution.
System-generated queries use allow_schema_inspection=False (no metadata access).
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from task_analyzer.core.security_guard import SecurityError
from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class SQLQuerySkill(BaseSkill):
    """Executes planned SQL queries and provides results as evidence."""

    name = "sql_query"
    display_name = "SQL Query Execution"
    description = "Executes planned read-only queries against configured databases"
    required_tools = ["DBReader"]

    async def run(self, task, context, security_guard, connectors, graph) -> dict[str, Any]:
        result: dict[str, Any] = {
            "query_results": [],
            "tables_queried": [],
            "row_counts": {},
        }

        start = time.time()

        try:
            # Get planned queries from context
            plan = context.get("investigation_plan")
            if not plan:
                logger.debug("sql_query_skipped", reason="no investigation plan")
                return result

            planned_queries = plan.queries if hasattr(plan, "queries") else plan.get("queries", [])
            planned_tables = plan.tables if hasattr(plan, "tables") else plan.get("tables", [])

            if not planned_queries and not planned_tables:
                return result

            # Find SQL connector
            db_connector = None
            for name, conn in connectors.items():
                conn_type = getattr(conn, "connector_type", None)
                type_val = conn_type.value if hasattr(conn_type, "value") else str(conn_type)
                if type_val == "sql_database":
                    db_connector = conn
                    break

            if not db_connector:
                logger.debug("sql_query_skipped", reason="no sql connector")
                return result

            security_guard.validate_tool("DBReader", "select_query")

            # Execute planned queries — ALL validated through SecurityGuard
            for i, query in enumerate(planned_queries[:5]):
                try:
                    # Extract table name for logging
                    import re
                    table_match = re.search(r'\[(\w+)\]', query)
                    table_name = table_match.group(1) if table_match else f"query_{i}"

                    # Validate query through SecurityGuard (read-only enforcement)
                    validated_query = security_guard.validate_sql_query(query)

                    engine = db_connector._get_engine()
                    from sqlalchemy import text
                    with engine.connect() as c:
                        c.execute(text("SET ROWCOUNT 20"))
                        c.execute(text("SET LOCK_TIMEOUT 5000"))
                        rows_raw = c.execute(text(validated_query))
                        rows = [dict(row._mapping) for row in rows_raw.fetchall()]

                    result["tables_queried"].append(table_name)
                    result["row_counts"][table_name] = len(rows)

                    # Store sample rows
                    sample = []
                    for row in rows[:5]:
                        sample_row = {}
                        for k, v in row.items():
                            sample_row[str(k)] = str(v)[:200]
                        sample.append(sample_row)

                    result["query_results"].append({
                        "table": table_name,
                        "query": validated_query,
                        "row_count": len(rows),
                        "sample_rows": sample,
                        "columns": list(rows[0].keys()) if rows else [],
                    })

                    # Add to graph
                    table_node = f"table:{table_name}"
                    graph.add_node(table_node, "database_table", {
                        "label": table_name,
                        "rows": len(rows),
                    })
                    graph.add_edge(task.id, table_node, "queries")

                    logger.info("sql_query_executed", table=table_name, rows=len(rows))

                except SecurityError as sec_err:
                    logger.warning("sql_query_blocked_by_guard", query=query[:80], error=str(sec_err))
                except Exception as exc:
                    logger.debug("sql_query_failed", query=query[:80], error=str(exc)[:100])

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "sql_query_skill_complete",
                task_id=task.id,
                tables=len(result["tables_queried"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("sql_query_skill_failed", task_id=task.id, error=str(exc))

        return result

    def is_available(self, connectors: dict) -> bool:
        for conn in connectors.values():
            conn_type = getattr(conn, "connector_type", None)
            if conn_type and hasattr(conn_type, "value") and conn_type.value == "sql_database":
                return True
        return False
