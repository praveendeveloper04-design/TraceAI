"""
TicketContextSkill — Expands ticket context by fetching related tasks
and extracting key entities.

This skill uses the TicketReader tool to:
  1. Search for related tickets by keywords
  2. Fetch details of linked/related tasks
  3. Extract key entities (services, components, people)
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class TicketContextSkill(BaseSkill):
    """Expands ticket context by fetching related tasks and extracting key entities."""

    name = "ticket_context"
    display_name = "Ticket Context Expansion"
    description = "Fetches related tasks and extracts key entities"
    required_tools = ["TicketReader"]

    async def run(
        self, task, context, security_guard, connectors, graph
    ) -> dict[str, Any]:
        """
        Execute ticket context expansion.

        Returns:
            Dict with keys: related_tasks, key_entities, timeline
        """
        result: dict[str, Any] = {
            "related_tasks": [],
            "key_entities": [],
            "timeline": [],
        }

        start = time.time()

        try:
            # Step 1: Validate tool access
            security_guard.validate_tool("TicketReader", "search")

            # Step 2: Search for related tickets using task keywords
            keywords = self._extract_search_terms(task.title)

            # Use ticket connector to search for related tasks
            ticket_connector = None
            for name, conn in connectors.items():
                conn_type = getattr(conn, "connector_type", None)
                if conn_type and hasattr(conn_type, "value"):
                    type_val = conn_type.value
                else:
                    type_val = str(conn_type)
                if type_val in ("azure_devops", "jira", "github_issues"):
                    ticket_connector = conn
                    break

            if ticket_connector and keywords:
                try:
                    security_guard.validate_tool("TicketReader", "search")
                    search_results = await ticket_connector.search(
                        " ".join(keywords[:5]), max_results=5
                    )
                    for sr in search_results:
                        related_id = sr.get("id", sr.get("external_id", "unknown"))
                        related_title = sr.get("title", "Unknown")
                        result["related_tasks"].append({
                            "id": related_id,
                            "title": related_title,
                        })
                        # Add to graph
                        graph.add_node(str(related_id), "ticket", {
                            "title": related_title,
                        })
                        graph.add_edge(task.id, str(related_id), "related_to")
                except Exception as exc:
                    logger.warning(
                        "ticket_search_failed",
                        task_id=task.id,
                        error=str(exc),
                    )

            # Step 3: Extract key entities from task description
            security_guard.validate_tool("TicketReader", "get_context")
            entities = self._extract_entities(task.title, task.description)
            result["key_entities"] = entities

            # Add entity nodes to graph
            for entity in entities:
                entity_id = f"entity:{entity['name']}"
                graph.add_node(entity_id, entity["type"], {
                    "name": entity["name"],
                })
                graph.add_edge(task.id, entity_id, "mentions")

            # Step 4: Build timeline from task metadata
            if task.created_at:
                result["timeline"].append({
                    "event": "task_created",
                    "timestamp": str(task.created_at),
                })
            if task.updated_at:
                result["timeline"].append({
                    "event": "task_updated",
                    "timestamp": str(task.updated_at),
                })

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "ticket_context_complete",
                task_id=task.id,
                related_count=len(result["related_tasks"]),
                entities_count=len(result["key_entities"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("ticket_context_failed", task_id=task.id, error=str(exc))

        return result

    @staticmethod
    def _extract_search_terms(title: str) -> list[str]:
        """Extract meaningful search terms from task title."""
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", title)
        stop_words = {"the", "a", "an", "is", "are", "in", "on", "for", "to", "and", "or", "not"}
        return [w for w in words if len(w) > 2 and w.lower() not in stop_words]

    @staticmethod
    def _extract_entities(title: str, description: str) -> list[dict[str, str]]:
        """Extract key entities (services, components, people) from text."""
        text = f"{title} {description}"
        entities: list[dict[str, str]] = []

        # Detect service names (common patterns)
        service_patterns = re.findall(
            r"\b([a-z]+-(?:service|api|worker|gateway|proxy|server))\b",
            text.lower(),
        )
        for svc in set(service_patterns):
            entities.append({"name": svc, "type": "service"})

        # Detect file paths
        file_patterns = re.findall(
            r"\b([a-zA-Z_/\\]+\.(?:py|ts|js|java|cs|go|rs|rb|sql|yaml|yml|json))\b",
            text,
        )
        for fp in set(file_patterns):
            entities.append({"name": fp, "type": "file"})

        # Detect error codes / HTTP status codes
        error_patterns = re.findall(r"\b([45]\d{2})\b", text)
        for err in set(error_patterns):
            entities.append({"name": f"HTTP {err}", "type": "error_code"})

        return entities
