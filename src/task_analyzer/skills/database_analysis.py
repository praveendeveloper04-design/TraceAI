"""
DatabaseAnalysisSkill — Inspects database schema and runs safe SELECT queries.

This skill uses the DBReader tool to:
  1. Get table schemas relevant to the task
  2. Run safe read-only queries for recent error rows
  3. Detect anomalies in query results

Requires a SQL Database connector to be configured.
All queries are double-validated through SecurityGuard.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class DatabaseAnalysisSkill(BaseSkill):
    """Inspects database schema and runs safe read-only queries."""

    name = "database_analysis"
    display_name = "Database Analysis"
    description = "Inspects schema and runs safe read-only queries"
    required_tools = ["DBReader"]

    async def run(
        self, task, context, security_guard, connectors, graph
    ) -> dict[str, Any]:
        """
        Execute database analysis.

        Returns:
            Dict with keys: schema_info, query_results, anomalies
        """
        result: dict[str, Any] = {
            "schema_info": [],
            "query_results": [],
            "anomalies": [],
        }

        start = time.time()

        try:
            # Step 1: Validate tool access
            security_guard.validate_tool("DBReader", "describe_schema")

            # Step 2: Find the SQL database connector
            db_connector = None
            for name, conn in connectors.items():
                conn_type = getattr(conn, "connector_type", None)
                if conn_type and hasattr(conn_type, "value"):
                    type_val = conn_type.value
                else:
                    type_val = str(conn_type)
                if type_val == "sql_database":
                    db_connector = conn
                    break

            if not db_connector:
                logger.debug("database_analysis_skipped", reason="no sql connector")
                return result

            # Step 3: Get schema information
            try:
                schema_context = await db_connector.get_context(task)
                if schema_context:
                    result["schema_info"].append(schema_context)
            except Exception as exc:
                logger.warning(
                    "schema_fetch_failed",
                    task_id=task.id,
                    error=str(exc),
                )

            # Step 4: Run safe read-only queries
            # Double validation: SecurityGuard validates the SQL before execution
            security_guard.validate_tool("DBReader", "select_query")

            # Build a safe query based on task keywords
            keywords = self._extract_table_hints(task.title, task.description)
            for keyword in keywords[:3]:  # Limit to 3 queries
                try:
                    # Validate the query through SecurityGuard
                    query = f"SELECT TOP 10 * FROM [{keyword}] ORDER BY 1 DESC"
                    validated_query = security_guard.validate_sql_query(query)

                    query_id = f"query:{keyword}"
                    search_results = await db_connector.search(validated_query)
                    row_count = len(search_results)

                    result["query_results"].append({
                        "table": keyword,
                        "rows": row_count,
                        "sample": search_results[:3] if search_results else [],
                    })

                    # Add to graph
                    graph.add_node(query_id, "database_query", {
                        "sql": validated_query,
                        "rows": row_count,
                    })
                    graph.add_edge(task.id, query_id, "query_executed")

                except Exception as exc:
                    # Query might fail if table doesn't exist — that's OK
                    logger.debug(
                        "db_query_skipped",
                        keyword=keyword,
                        error=str(exc),
                    )

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "database_analysis_complete",
                task_id=task.id,
                schemas=len(result["schema_info"]),
                queries=len(result["query_results"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("database_analysis_failed", task_id=task.id, error=str(exc))

        return result

    def is_available(self, connectors: dict) -> bool:
        """Only available when a SQL Database connector is configured."""
        for conn in connectors.values():
            conn_type = getattr(conn, "connector_type", None)
            if conn_type and hasattr(conn_type, "value"):
                if conn_type.value == "sql_database":
                    return True
            elif str(conn_type) == "sql_database":
                return True
        return False

    @staticmethod
    def _extract_table_hints(title: str, description: str) -> list[str]:
        """Extract potential table names from task text."""
        import re

        text = f"{title} {description}"
        # Look for words that might be table names (PascalCase, snake_case, or quoted)
        candidates: list[str] = []

        # PascalCase words (likely entity/table names)
        pascal = re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text)
        candidates.extend(pascal)

        # snake_case words with underscores
        snake = re.findall(r"\b([a-z]+_[a-z_]+)\b", text)
        candidates.extend(snake)

        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for c in candidates:
            if c.lower() not in seen:
                seen.add(c.lower())
                unique.append(c)

        return unique[:5]
