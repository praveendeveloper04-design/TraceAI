"""
Core models — Pydantic data models used across the entire platform.

These models define the canonical shapes for tasks, investigations,
connector configs, project profiles, and investigation reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class TaskType(str, Enum):
    BUG = "bug"
    USER_STORY = "user_story"
    INCIDENT = "incident"
    TASK = "task"
    FEATURE = "feature"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ConnectorType(str, Enum):
    AZURE_DEVOPS = "azure_devops"
    JIRA = "jira"
    GITHUB_ISSUES = "github_issues"
    CONFLUENCE = "confluence"
    SALESFORCE = "salesforce"
    SQL_DATABASE = "sql_database"
    MCP = "mcp"
    GRAFANA = "grafana"
    CUSTOM = "custom"


class InvestigationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# ─── Task Models ──────────────────────────────────────────────────────────────

class TaskComment(BaseModel):
    """A comment on a task from the ticket system."""
    author: str
    content: str
    created_at: datetime | None = None


class TaskAttachment(BaseModel):
    """An attachment linked to a task."""
    name: str
    url: str
    content_type: str | None = None


class Task(BaseModel):
    """
    Canonical task representation — normalized from any ticket source.

    Every connector maps its native ticket format into this model so the
    investigation engine works identically regardless of the source system.
    """
    id: str
    source: ConnectorType
    external_id: str
    title: str
    description: str = ""
    task_type: TaskType = TaskType.UNKNOWN
    status: TaskStatus = TaskStatus.UNKNOWN
    severity: Severity = Severity.UNKNOWN
    assigned_to: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    comments: list[TaskComment] = Field(default_factory=list)
    attachments: list[TaskAttachment] = Field(default_factory=list)
    related_tasks: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @property
    def full_context(self) -> str:
        """Build a rich text context for the AI from all task fields."""
        parts = [
            f"# {self.title}",
            f"**Type**: {self.task_type.value}  |  **Severity**: {self.severity.value}  |  **Status**: {self.status.value}",
            "",
            self.description,
        ]
        if self.comments:
            parts.append("\n## Comments")
            for c in self.comments:
                parts.append(f"**{c.author}** ({c.created_at}):\n{c.content}\n")
        if self.tags:
            parts.append(f"\n**Tags**: {', '.join(self.tags)}")
        return "\n".join(parts)


# ─── Project Knowledge ────────────────────────────────────────────────────────

class ServiceInfo(BaseModel):
    """A discovered service or module in the repository."""
    name: str
    path: str
    language: str | None = None
    framework: str | None = None
    description: str = ""
    entry_points: list[str] = Field(default_factory=list)


class DatabaseModel(BaseModel):
    """A discovered database model or table."""
    name: str
    source_file: str | None = None
    fields: list[str] = Field(default_factory=list)


class ProjectProfile(BaseModel):
    """
    Lightweight knowledge profile generated from scanning a repository.

    Stored locally so the AI doesn't need to rediscover the project
    structure on every investigation.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    repo_path: str
    repo_name: str
    primary_language: str | None = None
    languages: dict[str, float] = Field(default_factory=dict)  # lang -> percentage
    services: list[ServiceInfo] = Field(default_factory=list)
    database_models: list[DatabaseModel] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
    directory_tree: str = ""
    summary: str = ""
    scanned_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def context_summary(self) -> str:
        """Produce a compact summary for injection into AI prompts."""
        lines = [
            f"## Project: {self.repo_name}",
            f"**Primary Language**: {self.primary_language or 'Unknown'}",
            f"**Languages**: {', '.join(f'{k} ({v:.0%})' for k, v in self.languages.items())}",
        ]
        if self.services:
            lines.append("\n### Services / Modules")
            for svc in self.services:
                lines.append(f"- **{svc.name}** (`{svc.path}`) — {svc.description}")
        if self.database_models:
            lines.append("\n### Database Models")
            for db in self.database_models:
                lines.append(f"- **{db.name}**: {', '.join(db.fields[:8])}")
        lines.append(f"\n### Directory Structure\n```\n{self.directory_tree}\n```")
        return "\n".join(lines)


# ─── Investigation ────────────────────────────────────────────────────────────

class InvestigationStep(BaseModel):
    """A single step in the AI investigation chain."""
    step_number: int
    action: str
    tool_used: str | None = None
    input_summary: str = ""
    output_summary: str = ""
    reasoning: str = ""
    duration_ms: int | None = None


class InvestigationFinding(BaseModel):
    """A concrete finding from the investigation."""
    category: str  # e.g. "root_cause", "related_code", "configuration_issue"
    title: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence: list[str] = Field(default_factory=list)
    file_references: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    """
    The final structured output of an AI investigation.

    Contains the full reasoning chain, findings, and recommendations.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    task_title: str
    status: InvestigationStatus = InvestigationStatus.PENDING
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    steps: list[InvestigationStep] = Field(default_factory=list)
    findings: list[InvestigationFinding] = Field(default_factory=list)
    summary: str = ""
    root_cause: str = ""
    recommendations: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    raw_llm_output: str = ""
    tokens_used: int = 0
    model_used: str = ""
    error: str | None = None
    investigation_graph: dict | None = None          # Investigation relationship graph
    root_cause_node_id: str | None = None            # Primary root cause node in graph
    root_cause_hypotheses: list[dict] | None = None  # Ranked root cause hypotheses
    evidence_summary: dict | None = None             # Aggregated evidence from skills

    def to_markdown(self) -> str:
        """Render the report as a Markdown document."""
        md = [
            f"# Investigation Report: {self.task_title}",
            f"**Task ID**: {self.task_id}  |  **Status**: {self.status.value}",
            f"**Started**: {self.started_at}  |  **Completed**: {self.completed_at or 'N/A'}",
            f"**Model**: {self.model_used}  |  **Tokens**: {self.tokens_used}",
            "",
            "---",
            "",
            "## Summary",
            self.summary,
            "",
        ]
        if self.root_cause:
            md.extend(["## Root Cause Analysis", self.root_cause, ""])

        if self.findings:
            md.append("## Findings")
            for i, f in enumerate(self.findings, 1):
                md.append(f"\n### {i}. {f.title} ({f.category})")
                md.append(f"**Confidence**: {f.confidence:.0%}")
                md.append(f"\n{f.description}")
                if f.file_references:
                    md.append(f"\n**Files**: {', '.join(f'`{r}`' for r in f.file_references)}")
                if f.evidence:
                    md.append("\n**Evidence**:")
                    for e in f.evidence:
                        md.append(f"- {e}")
            md.append("")

        if self.recommendations:
            md.append("## Recommendations")
            for r in self.recommendations:
                md.append(f"- {r}")
            md.append("")

        if self.affected_files:
            md.append("## Affected Files")
            for af in self.affected_files:
                md.append(f"- `{af}`")
            md.append("")

        if self.steps:
            md.append("## Investigation Steps")
            for s in self.steps:
                md.append(f"\n### Step {s.step_number}: {s.action}")
                if s.tool_used:
                    md.append(f"**Tool**: {s.tool_used}")
                md.append(f"\n{s.reasoning}")
            md.append("")

        if self.root_cause_hypotheses:
            md.append("## Root Cause Hypotheses")
            for i, h in enumerate(self.root_cause_hypotheses, 1):
                confidence = h.get("confidence", 0)
                md.append(f"\n### {i}. {h.get('description', 'Unknown')}")
                md.append(f"**Confidence**: {confidence:.0%}")
                evidence = h.get("evidence", [])
                if evidence:
                    md.append("\n**Evidence**:")
                    for e in evidence:
                        md.append(f"- {e}")
            md.append("")

        if self.investigation_graph:
            stats = self.investigation_graph.get("stats", {})
            md.append("## Investigation Graph")
            md.append(f"- **Nodes**: {stats.get('node_count', 0)}")
            md.append(f"- **Edges**: {stats.get('edge_count', 0)}")
            md.append("")

        return "\n".join(md)


# ─── Configuration ────────────────────────────────────────────────────────────

class ConnectorConfig(BaseModel):
    """Configuration for a single connector instance."""
    connector_type: ConnectorType
    name: str
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)
    # Credential keys are stored in keychain, referenced by name here
    credential_keys: list[str] = Field(default_factory=list)


class PlatformConfig(BaseModel):
    """Top-level platform configuration — persisted to disk (no secrets)."""
    config_version: str = "1.0"                       # Config schema version for migrations
    version: str = "1.0"
    mode: str = "safe"                                # "safe" (default) or "developer"
    background_refresh: bool = True                   # Enable 5-min auto-refresh
    repositories: list[str] = Field(default_factory=list)
    ticket_source: ConnectorConfig | None = None
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    llm_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 8192
    investigation_max_steps: int = 15
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Config Migration ────────────────────────────────────────────────────────

CURRENT_CONFIG_VERSION = "1.0"


def migrate_config(data: dict, from_version: str) -> dict:
    """
    Migrate config data from an older version to the current version.

    Add migration steps here as the config schema evolves.
    """
    # v0.0 -> v1.0: Add new fields with defaults
    if from_version < "1.0":
        data.setdefault("config_version", "1.0")
        data.setdefault("mode", "safe")
        data.setdefault("background_refresh", True)

    data["config_version"] = CURRENT_CONFIG_VERSION
    return data
