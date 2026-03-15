"""
SQLQuerySkill -- Executes planned read-only SQL queries during investigation.

Unlike DatabaseAnalysisSkill which guesses table names from task keywords,
this skill executes specific queries determined by the InvestigationPlanner.
The planner identifies relevant tables from the system map, and this skill
fetches recent rows from those tables.

All queries are read-only and validated through SecurityGuard.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

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

            # Execute each planned query
            for table in planned_tables[:5]:
                query = f"SELECT TOP 10 * FROM [{table}] ORDER BY 1 DESC"
                try:
                    # Validate through SecurityGuard
                    validated = security_guard.validate_sql_query(query)
                    rows = db_connector.execute_query(validated)

                    result["tables_queried"].append(table)
                    result["row_counts"][table] = len(rows)

                    # Store sample rows (limit data size)
                    sample = []
                    for row in rows[:5]:
                        sample_row = {}
                        for k, v in row.items():
                            sample_row[str(k)] = str(v)[:200]
                        sample.append(sample_row)

                    result["query_results"].append({
                        "table": table,
                        "query": query,
                        "row_count": len(rows),
                        "sample_rows": sample,
                        "columns": list(rows[0].keys()) if rows else [],
                    })

                    # Add to graph
                    table_node = f"table:{table}"
                    graph.add_node(table_node, "database_table", {
                        "label": table,
                        "rows": len(rows),
                    })
                    graph.add_edge(task.id, table_node, "queries")

                    query_node = f"sql_query:{table}"
                    graph.add_node(query_node, "sql_query", {
                        "label": f"SELECT FROM {table}",
                        "rows": len(rows),
                    })
                    graph.add_edge(query_node, table_node, "queries")

                    logger.info("sql_query_executed", table=table, rows=len(rows))

                except Exception as exc:
                    logger.debug("sql_query_table_failed", table=table, error=str(exc))

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
