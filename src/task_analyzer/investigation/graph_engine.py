"""
Investigation Graph Engine — Lightweight in-memory graph for tracking
investigation relationships and causal chains.

The Evidence Graph models how the root cause was discovered by tracking
relationships between:

  Ticket -> Repository Files -> Functions
  Ticket -> Database Tables -> SQL Queries
  Evidence -> Hypotheses

Node types: ticket, file, function, database_table, sql_query,
            evidence, hypothesis, git_commit, log_entry, service

Edge relationships: references, queries, modifies, depends_on,
                    produces_evidence, supports_hypothesis, related_to,
                    modified_by, generated_error, causes, triggers,
                    impacts, explains

Node IDs use type prefixes for global uniqueness:
  ticket:{id}, file:{path}, commit:{sha}, evidence:{hash},
  hypothesis:{id}, table:{name}, function:{name}

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


# ─── Typed Constants ──────────────────────────────────────────────────────────

class NodeType:
    TICKET = "ticket"
    FILE = "file"
    FUNCTION = "function"
    DATABASE_TABLE = "database_table"
    SQL_QUERY = "sql_query"
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"
    GIT_COMMIT = "git_commit"
    LOG_ENTRY = "log_entry"
    SERVICE = "service"


class EdgeRelation:
    # Discovery relationships
    REFERENCES = "references"
    QUERIES = "queries"
    MODIFIES = "modifies"
    DEPENDS_ON = "depends_on"
    PRODUCES_EVIDENCE = "produces_evidence"
    SUPPORTS_HYPOTHESIS = "supports_hypothesis"
    RELATED_TO = "related_to"
    MODIFIED_BY = "modified_by"
    GENERATED_ERROR = "generated_error"
    MENTIONS = "mentions"
    # Causal relationships
    CAUSES = "causes"
    TRIGGERS = "triggers"
    IMPACTS = "impacts"
    EXPLAINS = "explains"

    # Set of causal edge types for chain detection
    CAUSAL = frozenset({"causes", "triggers", "impacts", "explains"})


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class InvestigationNode:
    """A single entity discovered during investigation."""
    id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvestigationEdge:
    """A relationship between two investigation entities."""
    source: str
    target: str
    relation: str


# ─── Graph ────────────────────────────────────────────────────────────────────

class InvestigationGraph:
    """
    Lightweight in-memory graph for tracking investigation relationships.
    """

    MAX_NODES = 500
    MAX_EDGES = 2000

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, str]] = []
        self._edge_set: set[tuple[str, str, str]] = set()  # for dedup
        self.root_cause_node_id: str | None = None

    def add_node(
        self, node_id: str, node_type: str, data: dict[str, Any] | None = None
    ) -> None:
        """Add a node if it doesn't already exist."""
        if node_id not in self.nodes and len(self.nodes) < self.MAX_NODES:
            node_data = data or {}
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "label": node_data.get("label", node_id),
                "confidence": node_data.get("confidence", None),
                "is_root_cause": False,
                "data": node_data,
            }

    def add_edge(self, source: str, target: str, relation: str) -> None:
        """Add a directed edge. Duplicates are silently skipped."""
        key = (source, target, relation)
        if key in self._edge_set:
            return
        if len(self.edges) < self.MAX_EDGES:
            self.edges.append({
                "source": source,
                "target": target,
                "relationship": relation,
            })
            self._edge_set.add(key)

    def mark_root_cause(self, node_id: str) -> None:
        """Mark a node as the primary root cause."""
        node = self.nodes.get(node_id)
        if node:
            node["is_root_cause"] = True
            self.root_cause_node_id = node_id

    def set_confidence(self, node_id: str, confidence: float) -> None:
        """Set or update the confidence score on a node."""
        node = self.nodes.get(node_id)
        if node:
            node["confidence"] = round(confidence, 2)
            node["data"]["confidence"] = round(confidence, 2)

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

    def get_nodes(self) -> list[dict[str, Any]]:
        """Get all nodes."""
        return list(self.nodes.values())

    def get_edges(self) -> list[dict[str, str]]:
        """Get all edges."""
        return list(self.edges)

    def find_causal_chains(self) -> list[list[str]]:
        """
        Find causal chains in the graph by following causal edges.

        Returns a list of chains, where each chain is a list of node labels.
        Chains are sorted longest-first.
        """
        causal_edges: dict[str, list[str]] = {}
        for e in self.edges:
            if e["relationship"] in EdgeRelation.CAUSAL:
                src = e["source"]
                if src not in causal_edges:
                    causal_edges[src] = []
                causal_edges[src].append(e["target"])

        if not causal_edges:
            return []

        # DFS to find all chains
        chains: list[list[str]] = []

        def _walk(node_id: str, path: list[str]) -> None:
            targets = causal_edges.get(node_id, [])
            if not targets:
                if len(path) >= 2:
                    chains.append(path[:])
                return
            for tgt in targets:
                if tgt not in path:  # prevent cycles
                    path.append(tgt)
                    _walk(tgt, path)
                    path.pop()

        for start in causal_edges:
            _walk(start, [start])

        chains.sort(key=len, reverse=True)
        return chains

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict."""
        # Build causal chains for the output
        causal_chains = []
        for chain in self.find_causal_chains()[:5]:
            labels = []
            for nid in chain:
                node = self.nodes.get(nid)
                labels.append(node["label"] if node else nid)
            causal_chains.append(labels)

        return {
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
            "root_cause_node_id": self.root_cause_node_id,
            "causal_chains": causal_chains,
            "stats": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "node_types": self._count_by_type(),
            },
        }

    def export(self) -> dict[str, Any]:
        """Alias for to_dict() — backward compatibility."""
        return self.to_dict()

    def summarize(self) -> str:
        """
        Produce a text summary of the graph for inclusion in LLM context.
        Includes entity counts, causal chains, and root cause marker.
        """
        parts = ["## Investigation Graph Summary"]
        parts.append(f"Entities: {len(self.nodes)}, Relationships: {len(self.edges)}")

        # Root cause
        if self.root_cause_node_id:
            rc_node = self.nodes.get(self.root_cause_node_id, {})
            conf = rc_node.get("confidence")
            conf_str = f" (confidence: {conf:.0%})" if conf else ""
            parts.append(f"\n**Root Cause**: {rc_node.get('label', 'Unknown')}{conf_str}")

        # Entity counts
        type_counts = self._count_by_type()
        if type_counts:
            parts.append("\n### Entities by Type")
            for ntype, count in sorted(type_counts.items()):
                parts.append(f"- {ntype}: {count}")

        # Causal chains
        chains = self.find_causal_chains()
        if chains:
            parts.append("\n### Causal Chains")
            for chain in chains[:3]:
                labels = []
                for nid in chain:
                    node = self.nodes.get(nid)
                    labels.append(node["label"] if node else nid)
                parts.append(f"- {' -> '.join(labels)}")

        # Key relationships (non-causal)
        if self.edges:
            parts.append("\n### Key Relationships")
            seen: set[str] = set()
            for edge in self.edges[:15]:
                src_node = self.nodes.get(edge["source"], {})
                tgt_node = self.nodes.get(edge["target"], {})
                src_label = src_node.get("label", edge["source"])
                tgt_label = tgt_node.get("label", edge["target"])
                rel = edge["relationship"]
                chain = f"{src_label} --[{rel}]--> {tgt_label}"
                if chain not in seen:
                    parts.append(f"- {chain}")
                    seen.add(chain)

        return "\n".join(parts)

    def _count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            t = node["type"]
            counts[t] = counts.get(t, 0) + 1
        return counts


# ─── Graph Builder ────────────────────────────────────────────────────────────

class GraphBuilder:
    """
    Constructs the evidence graph deterministically from aggregated evidence.

    Called after EvidenceAggregator and before LLM reasoning.
    The graph is built entirely from skill outputs — never from LLM output.
    """

    def build(
        self,
        graph: InvestigationGraph,
        task_id: str,
        evidence: dict[str, list],
        hypotheses: list,
    ) -> None:
        """
        Populate the graph with nodes and edges from evidence.
        The ticket node should already exist in the graph.
        """
        self._add_files(graph, task_id, evidence)
        self._add_commits(graph, evidence)
        self._add_errors(graph, task_id, evidence)
        self._add_log_entries(graph, task_id, evidence)
        self._add_db_anomalies(graph, task_id, evidence)
        self._add_related_tasks(graph, task_id, evidence)
        self._add_hypotheses(graph, task_id, evidence, hypotheses)
        self._build_causal_edges(graph, evidence)
        self._mark_root_cause(graph, hypotheses)

    # ── Node builders ─────────────────────────────────────────────────────

    def _add_files(self, graph: InvestigationGraph, task_id: str, evidence: dict) -> None:
        for f in evidence.get("files", []):
            label = f if isinstance(f, str) else str(f)
            node_id = f"file:{label}"
            graph.add_node(node_id, NodeType.FILE, {"label": label, "path": label})
            graph.add_edge(task_id, node_id, EdgeRelation.REFERENCES)

    def _add_commits(self, graph: InvestigationGraph, evidence: dict) -> None:
        for c in evidence.get("commits", []):
            if isinstance(c, dict):
                sha = c.get("sha", c.get("id", "unknown"))
                msg = c.get("message", "")
                node_id = f"commit:{sha}"
                graph.add_node(node_id, NodeType.GIT_COMMIT, {
                    "label": f"{sha[:8]}: {msg[:40]}", "sha": sha, "message": msg[:100],
                })
                for f in evidence.get("files", [])[:5]:
                    graph.add_edge(node_id, f"file:{f}", EdgeRelation.MODIFIES)

    def _add_errors(self, graph: InvestigationGraph, task_id: str, evidence: dict) -> None:
        for i, err in enumerate(evidence.get("errors", [])):
            err_str = str(err)[:100]
            node_id = f"evidence:error_{i}"
            graph.add_node(node_id, NodeType.EVIDENCE, {
                "label": err_str[:50], "detail": err_str,
            })
            graph.add_edge(task_id, node_id, EdgeRelation.PRODUCES_EVIDENCE)

    def _add_log_entries(self, graph: InvestigationGraph, task_id: str, evidence: dict) -> None:
        for i, entry in enumerate(evidence.get("log_entries", [])):
            if isinstance(entry, dict):
                msg = entry.get("message", "")[:80]
                level = entry.get("level", "info")
                node_id = f"evidence:log_{i}"
                graph.add_node(node_id, NodeType.EVIDENCE, {
                    "label": f"[{level}] {msg[:40]}", "level": level, "message": msg,
                })
                graph.add_edge(task_id, node_id, EdgeRelation.PRODUCES_EVIDENCE)

    def _add_db_anomalies(self, graph: InvestigationGraph, task_id: str, evidence: dict) -> None:
        for i, anomaly in enumerate(evidence.get("database_anomalies", [])):
            anom_str = str(anomaly)[:100]
            node_id = f"evidence:db_{i}"
            graph.add_node(node_id, NodeType.EVIDENCE, {
                "label": anom_str[:50], "detail": anom_str,
            })
            graph.add_edge(task_id, node_id, EdgeRelation.PRODUCES_EVIDENCE)

    def _add_related_tasks(self, graph: InvestigationGraph, task_id: str, evidence: dict) -> None:
        for rt in evidence.get("related_tasks", []):
            if isinstance(rt, dict):
                rt_id = rt.get("id", "unknown")
                rt_title = rt.get("title", "Unknown")
                node_id = f"ticket:{rt_id}"
                graph.add_node(node_id, NodeType.TICKET, {
                    "label": f"{rt_id}: {rt_title[:40]}",
                })
                graph.add_edge(task_id, node_id, EdgeRelation.RELATED_TO)

    def _add_hypotheses(
        self, graph: InvestigationGraph, task_id: str,
        evidence: dict, hypotheses: list,
    ) -> None:
        for i, h in enumerate(hypotheses):
            desc = h.description if hasattr(h, "description") else str(h)
            score = h.score if hasattr(h, "score") else 0.0
            node_id = f"hypothesis:{i}"
            graph.add_node(node_id, NodeType.HYPOTHESIS, {
                "label": desc[:60],
                "description": desc,
                "confidence": score,
            })
            graph.set_confidence(node_id, score)

            # Link supporting evidence to hypothesis
            h_evidence = h.evidence if hasattr(h, "evidence") else []
            linked = False
            for ev in h_evidence:
                for en_id, en_node in graph.nodes.items():
                    if en_id.startswith("evidence:"):
                        detail = str(en_node.get("data", {}).get("detail", ""))
                        if str(ev)[:20] in detail:
                            graph.add_edge(en_id, node_id, EdgeRelation.SUPPORTS_HYPOTHESIS)
                            linked = True
                            break
            if not linked:
                graph.add_edge(task_id, node_id, EdgeRelation.SUPPORTS_HYPOTHESIS)

    # ── Causal edge builder ───────────────────────────────────────────────

    def _build_causal_edges(self, graph: InvestigationGraph, evidence: dict) -> None:
        """
        Build causal edges deterministically from evidence patterns.

        Causal chain: commit -> causes -> file change
                      file   -> triggers -> error
                      error  -> explains -> hypothesis
        """
        commits = [n for n in graph.nodes.values() if n["type"] == NodeType.GIT_COMMIT]
        files = [n for n in graph.nodes.values() if n["type"] == NodeType.FILE]
        errors = [n for n in graph.nodes.values() if n["type"] == NodeType.EVIDENCE]
        hypotheses = [n for n in graph.nodes.values() if n["type"] == NodeType.HYPOTHESIS]

        # commit -> causes -> file (if commit modifies file)
        for commit in commits:
            for file_node in files:
                graph.add_edge(commit["id"], file_node["id"], EdgeRelation.CAUSES)

        # file -> triggers -> error (if both exist)
        for file_node in files[:3]:
            for error in errors[:3]:
                graph.add_edge(file_node["id"], error["id"], EdgeRelation.TRIGGERS)

        # error -> explains -> hypothesis
        for error in errors[:3]:
            for hyp in hypotheses[:2]:
                graph.add_edge(error["id"], hyp["id"], EdgeRelation.EXPLAINS)

    # ── Root cause marker ─────────────────────────────────────────────────

    def _mark_root_cause(self, graph: InvestigationGraph, hypotheses: list) -> None:
        """Mark the highest-confidence hypothesis as the root cause."""
        if not hypotheses:
            return

        best_idx = 0
        best_score = 0.0
        for i, h in enumerate(hypotheses):
            score = h.score if hasattr(h, "score") else 0.0
            if score > best_score:
                best_score = score
                best_idx = i

        root_id = f"hypothesis:{best_idx}"
        graph.mark_root_cause(root_id)
