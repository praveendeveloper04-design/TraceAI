"""
Root Cause Ranking Engine — Evaluates investigation findings and ranks
the most probable root causes.

Runs entirely on in-memory data — no external operations, no security bypass.

Inputs:
  - Investigation graph (nodes + edges)
  - Normalized evidence (from EvidenceAggregator)

Output:
  - Ranked list of RootCauseHypothesis sorted by confidence

Performance: Must run in <50ms using only in-memory data.
Security: NEVER performs external API calls or bypasses SecurityGuard.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RootCauseHypothesis:
    """A possible root cause discovered during investigation."""

    description: str
    evidence: list[str] = field(default_factory=list)  # supporting evidence items
    score: float = 0.0  # 0.0 to 1.0 confidence


class RootCauseEngine:
    """
    Analyzes investigation findings and ranks potential root causes.

    Inputs:
      - Investigation graph (nodes + edges)
      - Normalized evidence dict from EvidenceAggregator

    Output:
      - Ranked list of RootCauseHypothesis sorted by confidence

    Performance: Must run in <50ms using only in-memory data.
    Security: NEVER performs external API calls or bypasses SecurityGuard.
    """

    def analyze(
        self,
        graph: dict | None,
        evidence: dict,
    ) -> list[RootCauseHypothesis]:
        """
        Analyze findings and return ranked root cause hypotheses.

        Args:
            graph: Exported investigation graph (from InvestigationGraph.export())
            evidence: Normalized evidence dict from EvidenceAggregator.aggregate()
                      Expected keys: commits, errors, database_anomalies, files,
                      contributors, log_entries, related_tasks

        Returns:
            List of RootCauseHypothesis sorted by confidence (highest first)
        """
        hypotheses: list[RootCauseHypothesis] = []

        # Heuristic 1: Recent commits touching affected files
        self._analyze_commits(evidence, hypotheses)

        # Heuristic 2: Error patterns in logs
        self._analyze_errors(evidence, hypotheses)

        # Heuristic 3: Database anomalies
        self._analyze_database(evidence, hypotheses)

        # Heuristic 4: Graph connectivity — nodes with many edges are likely central
        self._analyze_graph_connectivity(graph, hypotheses)

        # Sort by confidence (highest first)
        hypotheses.sort(key=lambda h: h.score, reverse=True)
        return hypotheses

    @staticmethod
    def _analyze_commits(
        evidence: dict, hypotheses: list[RootCauseHypothesis]
    ) -> None:
        """Heuristic 1: Recent commits touching affected files."""
        commits = evidence.get("commits", [])
        if commits:
            commit_refs = []
            for c in commits[:3]:
                if isinstance(c, dict):
                    commit_refs.append(c.get("sha", c.get("id", "unknown")))
                else:
                    commit_refs.append(str(c))
            hypotheses.append(RootCauseHypothesis(
                description="Recent commit may have introduced regression",
                evidence=commit_refs,
                score=0.75,
            ))

    @staticmethod
    def _analyze_errors(
        evidence: dict, hypotheses: list[RootCauseHypothesis]
    ) -> None:
        """Heuristic 2: Error patterns in logs."""
        errors = evidence.get("errors", [])
        if errors:
            error_refs = [str(e)[:200] for e in errors[:3]]
            hypotheses.append(RootCauseHypothesis(
                description="Error patterns detected in service logs",
                evidence=error_refs,
                score=0.6,
            ))

    @staticmethod
    def _analyze_database(
        evidence: dict, hypotheses: list[RootCauseHypothesis]
    ) -> None:
        """Heuristic 3: Database anomalies."""
        anomalies = evidence.get("database_anomalies", [])
        if anomalies:
            anomaly_refs = [str(a)[:200] for a in anomalies[:3]]
            hypotheses.append(RootCauseHypothesis(
                description="Database anomalies may affect system behavior",
                evidence=anomaly_refs,
                score=0.4,
            ))

    @staticmethod
    def _analyze_graph_connectivity(
        graph: dict | None, hypotheses: list[RootCauseHypothesis]
    ) -> None:
        """Heuristic 4: Graph connectivity — highly connected nodes are likely central."""
        if not graph or not graph.get("nodes"):
            return

        edge_counts: dict[str, int] = {}
        for edge in graph.get("edges", []):
            source = edge.get("source", "")
            target = edge.get("target", "")
            edge_counts[source] = edge_counts.get(source, 0) + 1
            edge_counts[target] = edge_counts.get(target, 0) + 1

        # Nodes with highest connectivity are likely root causes
        for node in graph["nodes"]:
            node_id = node.get("id", "")
            node_type = node.get("type", "")
            count = edge_counts.get(node_id, 0)

            # Skip ticket nodes (they're the investigation target, not the cause)
            if count >= 3 and node_type != "ticket":
                hypotheses.append(RootCauseHypothesis(
                    description=f"Highly connected entity: {node_id} ({node_type})",
                    evidence=[f"Connected to {count} other findings"],
                    score=min(0.3 + count * 0.1, 0.9),
                ))
