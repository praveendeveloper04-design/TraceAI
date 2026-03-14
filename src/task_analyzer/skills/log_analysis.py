"""
LogAnalysisSkill — Searches logs for error patterns and correlates timestamps.

This skill uses the LogReader tool to:
  1. Search for error patterns related to the task
  2. Correlate timestamps around incident time
  3. Identify error frequency and patterns

Requires a Grafana connector to be configured.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class LogAnalysisSkill(BaseSkill):
    """Searches logs for error patterns and correlates timestamps."""

    name = "log_analysis"
    display_name = "Log Analysis"
    description = "Searches logs for error patterns and correlates timestamps"
    required_tools = ["LogReader"]

    async def run(
        self, task, context, security_guard, connectors, graph
    ) -> dict[str, Any]:
        """
        Execute log analysis.

        Returns:
            Dict with keys: error_patterns, log_entries, timeline_correlation
        """
        result: dict[str, Any] = {
            "error_patterns": [],
            "log_entries": [],
            "timeline_correlation": [],
        }

        start = time.time()

        try:
            # Step 1: Validate tool access
            security_guard.validate_tool("LogReader", "search_logs")

            # Step 2: Find the Grafana connector
            grafana_connector = None
            for name, conn in connectors.items():
                conn_type = getattr(conn, "connector_type", None)
                if conn_type and hasattr(conn_type, "value"):
                    type_val = conn_type.value
                else:
                    type_val = str(conn_type)
                if type_val == "grafana":
                    grafana_connector = conn
                    break

            if not grafana_connector:
                logger.debug("log_analysis_skipped", reason="no grafana connector")
                return result

            # Step 3: Search for error patterns related to task
            security_guard.validate_tool("LogReader", "query_logs")
            try:
                search_query = task.title
                search_results = await grafana_connector.search(
                    search_query, max_results=10
                )
                for sr in search_results:
                    log_id = sr.get("id", f"log_{len(result['log_entries'])}")
                    message = sr.get("message", sr.get("title", ""))
                    level = sr.get("level", "unknown")

                    result["log_entries"].append({
                        "id": log_id,
                        "message": message,
                        "level": level,
                    })

                    # Add to graph
                    graph.add_node(str(log_id), "log_entry", {
                        "level": level,
                        "message": str(message)[:200],
                    })

                    # If it's an error, also track the pattern
                    if level in ("error", "critical", "fatal"):
                        result["error_patterns"].append(str(message)[:200])

                        # Link to a service if identifiable
                        service_name = sr.get("service", sr.get("source"))
                        if service_name:
                            graph.add_node(str(service_name), "service", {
                                "name": str(service_name),
                            })
                            graph.add_edge(
                                str(service_name), str(log_id), "generated_error"
                            )

            except Exception as exc:
                logger.warning(
                    "log_search_failed",
                    task_id=task.id,
                    error=str(exc),
                )

            # Step 4: Correlate timestamps
            security_guard.validate_tool("LogReader", "get_dashboard")
            if task.created_at:
                result["timeline_correlation"].append({
                    "reference": "task_created",
                    "timestamp": str(task.created_at),
                    "log_entries_around": len(result["log_entries"]),
                })

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "log_analysis_complete",
                task_id=task.id,
                errors_found=len(result["error_patterns"]),
                entries_found=len(result["log_entries"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("log_analysis_failed", task_id=task.id, error=str(exc))

        return result

    def is_available(self, connectors: dict) -> bool:
        """Only available when a Grafana connector is configured."""
        for conn in connectors.values():
            conn_type = getattr(conn, "connector_type", None)
            if conn_type and hasattr(conn_type, "value"):
                if conn_type.value == "grafana":
                    return True
            elif str(conn_type) == "grafana":
                return True
        return False
