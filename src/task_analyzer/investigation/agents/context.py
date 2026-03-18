"""
Agent Context — Typed shared state for inter-agent communication.

Each agent writes a typed dataclass output to AgentContext.
Dependent agents read their dependencies' outputs from AgentContext.
The ParallelAnalysisEngine's wave system ensures ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Agent Output Dataclasses ─────────────────────────────────────────────────

@dataclass
class RepoScanOutput:
    """Output from RepoScanAgent — discovered code files."""
    code_files: list[dict] = field(default_factory=list)
    file_count: int = 0
    repos_scanned: int = 0


@dataclass
class SchemaDiscoveryOutput:
    """Output from SchemaDiscoveryAgent — DB schema + table data."""
    sql_tables: list[dict] = field(default_factory=list)
    sql_schema: list[dict] = field(default_factory=list)
    schema_info: dict[str, list[dict]] = field(default_factory=dict)
    tenant_db: str | None = None
    tables_with_data: int = 0


@dataclass
class CodeFlowOutput:
    """Output from CodeFlowAgent — wraps existing LayerMap."""
    layer_map: Any = None
    db_tables_referenced: list[str] = field(default_factory=list)


@dataclass
class TicketContextOutput:
    """Output from TicketContextAgent."""
    related_tasks: list[dict] = field(default_factory=list)
    key_entities: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)


@dataclass
class EntityExtractionOutput:
    """Output from EntityExtractionAgent."""
    entities: list[str] = field(default_factory=list)
    action_keywords: list[str] = field(default_factory=list)


@dataclass
class CodeDeepDiveOutput:
    """Output from CodeDeepDiveAgent — file content + targeted search."""
    code_files_with_content: list[dict] = field(default_factory=list)
    code_flows: list[dict] = field(default_factory=list)
    repo_search_results: list[dict] = field(default_factory=list)
    code_discovered_tables: list[str] = field(default_factory=list)


@dataclass
class SQLIntelligenceOutput:
    """Output from SQLIntelligenceAgent."""
    queries_generated: int = 0
    queries_executed: int = 0
    query_results: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)


@dataclass
class MergedEvidence:
    """Output from EvidenceMergeAgent — unified evidence for LLM."""
    code_files: list[dict] = field(default_factory=list)
    code_flows: list[dict] = field(default_factory=list)
    sql_tables: list[dict] = field(default_factory=list)
    sql_schema: list[dict] = field(default_factory=list)
    repo_search_results: list[dict] = field(default_factory=list)
    query_results: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    ticket_context: dict = field(default_factory=dict)
    entities: list[str] = field(default_factory=list)
    layer_map: Any = None
    quality: dict = field(default_factory=dict)
    loops_completed: int = 3
    tenant_db: str | None = None


# ── Agent Context ────────────────────────────────────────────────────────────

class AgentContext:
    """
    Typed wrapper around the shared agent output store.

    Thread-safe via the GIL for dict reads/writes.
    Agents write once, dependents read after wave completion.
    """

    def __init__(self) -> None:
        self._outputs: dict[str, Any] = {}

    def set(self, agent_name: str, output: Any) -> None:
        self._outputs[agent_name] = output

    def get(self, agent_name: str) -> Any | None:
        return self._outputs.get(agent_name)

    @property
    def repo_scan(self) -> RepoScanOutput | None:
        v = self._outputs.get("repo_scan")
        return v if isinstance(v, RepoScanOutput) else None

    @property
    def schema_discovery(self) -> SchemaDiscoveryOutput | None:
        v = self._outputs.get("schema_discovery")
        return v if isinstance(v, SchemaDiscoveryOutput) else None

    @property
    def code_flow(self) -> CodeFlowOutput | None:
        v = self._outputs.get("code_flow")
        return v if isinstance(v, CodeFlowOutput) else None

    @property
    def ticket_context(self) -> TicketContextOutput | None:
        v = self._outputs.get("ticket_context")
        return v if isinstance(v, TicketContextOutput) else None

    @property
    def entity_extraction(self) -> EntityExtractionOutput | None:
        v = self._outputs.get("entity_extraction")
        return v if isinstance(v, EntityExtractionOutput) else None

    @property
    def code_deep_dive(self) -> CodeDeepDiveOutput | None:
        v = self._outputs.get("code_deep_dive")
        return v if isinstance(v, CodeDeepDiveOutput) else None

    @property
    def sql_intelligence(self) -> SQLIntelligenceOutput | None:
        v = self._outputs.get("sql_intelligence")
        return v if isinstance(v, SQLIntelligenceOutput) else None

    @property
    def merged_evidence(self) -> MergedEvidence | None:
        v = self._outputs.get("evidence_merge")
        return v if isinstance(v, MergedEvidence) else None
