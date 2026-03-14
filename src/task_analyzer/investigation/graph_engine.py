"""
Investigation Graph Engine — Lightweight in-memory graph for tracking
investigation relationships.

The graph tracks relationships between investigation findings:
  Ticket -> Related Repository Files
  Repository File -> Git Commit
  Git Commit -> Deployment
  Deployment -> Log Errors
  Log Errors -> Database Anomalies

Performance constraints:
  - Max 500 nodes per investigation (recommended)
  - Max 2000 edges per investigation (recommended)
  - Entirely in-memory — discarded after investigation unless saved in report
  - NEVER performs external operations — only stores relationships

Security: The graph engine does NOT bypass SecurityGuard, Tool Permission
Registry, Rate Limiter, or Connector layer. It only records what skills
and tools have already discovered through validated channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestigationNode:
    """A single entity discovered during investigation."""

    id: str          # unique identifier
    type: str        # "ticket", "repository_file", "git_commit", "log_entry",
                     # "database_query", "service", "deployment"
    data: dict[str, Any] = field(default_factory=dict)  # type-specific metadata


@dataclass
class InvestigationEdge:
    """A relationship between two investigation entities."""

    source: str      # source node id
    target: str      # target node id
    relation: str    # "related_to", "modified_by", "generated_error",
                     # "queried_table", "affects_service"


class InvestigationGraph:
    """
    Lightweight in-memory graph for tracking investigation relationships.

    Example relationships discovered during analysis:
      Ticket -> Related Repository Files
      Repository File -> Git Commit
      Git Commit -> Deployment
      Deployment -> Log Errors
      Log Errors -> Database Anomalies

    Performance constraints:
      - Max 500 nodes per investigation (recommended)
      - Max 2000 edges per investigation (recommended)
      - Entirely in-memory — discarded after investigation unless saved in report
      - NEVER performs external operations — only stores relationships

    Security: The graph engine does NOT bypass SecurityGuard, Tool Permission
    Registry, Rate Limiter, or Connector layer. It only records what skills
    and tools have already discovered through validated channels.
    """

    # Recommended limits
    MAX_NODES = 500
    MAX_EDGES = 2000

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, str]] = []

    def add_node(
        self, node_id: str, node_type: str, data: dict[str, Any] | None = None
    ) -> None:
        """Add a node if it doesn't already exist."""
        if node_id not in self.nodes and len(self.nodes) < self.MAX_NODES:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "data": data or {},
            }

    def add_edge(self, source: str, target: str, relation: str) -> None:
        """Add a directed edge between two nodes."""
        if len(self.edges) < self.MAX_EDGES:
            self.edges.append({
                "source": source,
                "target": target,
                "relation": relation,
            })

    def get_neighbors(self, node_id: str) -> list[dict[str, str]]:
        """Get all edges connected to a node."""
        return [
            e for e in self.edges
            if e["source"] == node_id or e["target"] == node_id
        ]

    def get_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        """Get all nodes of a specific type."""
        return [n for n in self.nodes.values() if n["type"] == node_type]

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a specific node by ID."""
        return self.nodes.get(node_id)

    def get_edge_count(self, node_id: str) -> int:
        """Count edges connected to a node."""
        return sum(
            1 for e in self.edges
            if e["source"] == node_id or e["target"] == node_id
        )

    def export(self) -> dict[str, Any]:
        """Export the graph for inclusion in investigation reports."""
        return {
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
            "stats": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
            },
        }
