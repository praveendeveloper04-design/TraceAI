# TraceAI Architecture

TraceAI is a **deterministic-first AI investigation system**. It gathers evidence through structured skills before invoking LLM reasoning, ensuring investigations are grounded in real data rather than hallucination.

This document describes the system architecture for contributors and maintainers.

---

## 1. Overview

TraceAI helps developers investigate bugs, incidents, and user stories by:

1. **Collecting evidence** from ticket systems, repositories, logs, and databases
2. **Building a relationship graph** between discovered entities
3. **Ranking root causes** using heuristic analysis
4. **Reasoning with Claude** using the collected evidence as context
5. **Producing structured reports** with findings, confidence scores, and recommendations

The key design principle: **deterministic data collection happens before LLM reasoning**. Skills gather facts; Claude interprets them.

---

## 2. Investigation Pipeline

```
VS Code Extension
│
│  activate() → mainFlow() → ensureRunning()
│  Load cached tasks → fetch fresh tasks → populate sidebar
│
▼
FastAPI Backend (localhost:7420)
│
│  POST /api/investigate { task_id }
│
▼
Investigation Engine
│
├─ 1. Skills (deterministic data collection)
│     ├─ RepoAnalysisSkill     → files, commits, contributors
│     ├─ TicketContextSkill    → related tasks, entities, timeline
│     ├─ LogAnalysisSkill      → error patterns, log entries
│     └─ DatabaseAnalysisSkill → schema info, query results
│
├─ 2. Evidence Aggregator
│     Normalizes skill outputs into unified structure:
│     { files, commits, contributors, errors, log_entries,
│       database_anomalies, related_tasks }
│
├─ 3. Investigation Graph
│     In-memory graph tracking entity relationships:
│     Ticket → File → Commit → Service → Log Entry → DB Query
│
├─ 4. Root Cause Engine
│     Heuristic ranking before LLM reasoning:
│     - Recent commits (0.75 confidence)
│     - Error patterns (0.60 confidence)
│     - Database anomalies (0.40 confidence)
│     - Graph connectivity (variable)
│
├─ 5. Claude LLM Reasoning
│     LangChain agent with tool calling.
│     Receives: task context + evidence summary + project profile
│     Produces: structured JSON findings
│
▼
Investigation Report
  { summary, root_cause, findings[], recommendations[],
    investigation_graph, root_cause_hypotheses[], evidence_summary }
```

Each skill runs with a **30-second timeout**. If a skill hangs, it is skipped and the investigation continues with the remaining evidence.

---

## 3. Security Model

TraceAI enforces a strict **read-only security model**. Every operation passes through three layers:

```
Operation Request
      │
      ▼
┌─────────────────────────┐
│    SecurityGuard         │  Validates tool + operation against registry
│    Tool Permission       │  Blocks unregistered tools
│    Registry              │  Enforces Safe Mode (default)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│    Rate Limiter          │  Min intervals: tickets 30s, DB 5s, logs 10s
│                          │  Timeout protection: repo 10s, DB 30s
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│    Audit Logger          │  Logs every operation to ~/.traceai/logs/
└─────────────────────────┘
```

**What is blocked:**

| Category | Blocked Operations |
|----------|--------------------|
| Git | commit, push, reset, clean, checkout, rebase, merge, stash, rm, mv |
| SQL | INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, EXEC, GRANT, REVOKE |
| Files | Any write, append, or delete operation |
| Code | No subprocess, exec, or eval in the investigation engine |

**Tool Permission Registry** — every tool must be declared with explicit permissions:

```python
TOOL_REGISTRY = {
    "RepoReader":   ToolPermission(permission="read", safe=True, ...),
    "TicketReader":  ToolPermission(permission="read", safe=True, ...),
    "LogReader":     ToolPermission(permission="read", safe=True, ...),
    "DBReader":      ToolPermission(permission="read", safe=True, ...),
}
```

Unregistered tools are blocked unconditionally. Safe Mode (enabled by default) restricts execution to tools marked `safe=True`.

---

## 4. Skill System

Skills are reusable investigation workflows that collect evidence before LLM reasoning. Each skill:

- Subclasses `BaseSkill`
- Declares required tools from the registry
- Validates all operations through `SecurityGuard`
- Populates the `InvestigationGraph` with discovered relationships
- Returns a typed dict of findings

### Creating a New Skill

```python
from task_analyzer.skills.base_skill import BaseSkill

class MyCustomSkill(BaseSkill):
    name = "my_custom"
    display_name = "My Custom Analysis"
    description = "Analyzes something specific"
    required_tools = ["RepoReader"]  # Must exist in TOOL_REGISTRY

    async def run(self, task, context, security_guard, connectors, graph):
        security_guard.validate_tool("RepoReader", "list_files")
        # ... gather evidence ...
        graph.add_node("finding_id", "custom_type", {"key": "value"})
        graph.add_edge(task.id, "finding_id", "related_to")
        return {"custom_findings": [...]}

    def is_available(self, connectors):
        # Return False if required connectors are missing
        return True
```

Register in `InvestigationEngine.__init__()`:

```python
self.skill_registry.register(MyCustomSkill())
```

### Built-in Skills

| Skill | Purpose | Required Connector |
|-------|---------|--------------------|
| `RepoAnalysisSkill` | Files, git history, contributors | None (uses profiles) |
| `TicketContextSkill` | Related tasks, entity extraction | Ticket source |
| `LogAnalysisSkill` | Error patterns, timestamp correlation | Grafana |
| `DatabaseAnalysisSkill` | Schema inspection, safe queries | SQL Database |

---

## 5. Investigation Graph

The `InvestigationGraph` is a lightweight in-memory directed graph that tracks relationships between entities discovered during investigation.

### Node Types

| Type | Example |
|------|---------|
| `ticket` | The task being investigated |
| `repository_file` | A source file related to the issue |
| `git_commit` | A commit that modified relevant files |
| `log_entry` | An error log entry |
| `service` | A service that generated errors |
| `database_query` | A query executed during analysis |

### Edge Relations

| Relation | Meaning |
|----------|---------|
| `related_to` | General association |
| `modified_by` | File was changed by a commit |
| `generated_error` | Service produced an error log |
| `query_executed` | Investigation ran a database query |
| `mentions` | Task text references an entity |

### Constraints

- Max 500 nodes, 2000 edges per investigation
- Entirely in-memory — discarded after investigation unless exported to report
- Never performs external operations — only records what skills discovered
- Does not bypass SecurityGuard or any security layer

The graph is exported into the investigation report as `investigation_graph` for downstream analysis.

---

## 6. Root Cause Engine

The `RootCauseEngine` ranks potential root causes **before** LLM reasoning, using four heuristics applied to the normalized evidence from `EvidenceAggregator`:

| # | Heuristic | Input | Base Score |
|---|-----------|-------|------------|
| 1 | Recent commits | `evidence["commits"]` | 0.75 |
| 2 | Error patterns | `evidence["errors"]` | 0.60 |
| 3 | Database anomalies | `evidence["database_anomalies"]` | 0.40 |
| 4 | Graph connectivity | Nodes with 3+ edges | 0.30 + 0.10 per edge (max 0.90) |

Hypotheses are sorted by confidence score (highest first) and included in the report as `root_cause_hypotheses`. The LLM receives these rankings as additional context to inform its reasoning.

**Performance constraint:** Must complete in <50ms on in-memory data only.

---

## 7. VS Code Extension Flow

```
activate(context)
│
├─ Initialize services
│   ├─ ApiService (HTTP client to backend)
│   ├─ ServerManager (auto-start + crash recovery)
│   ├─ StateManager (globalState persistence)
│   ├─ TaskCache (~/.traceai/cache/tasks.json)
│   ├─ TaskTreeProvider (sidebar with status grouping)
│   └─ InvestigationTreeProvider (history sidebar)
│
├─ Register commands (traceai.*)
│
├─ mainFlow()
│   ├─ Status bar: "TraceAI: Starting..."
│   ├─ serverManager.ensureRunning()
│   │   ├─ GET /api/health → alive? done
│   │   ├─ Spawn: python -m task_analyzer.api.server
│   │   ├─ Poll /api/health every 1s (up to 30 attempts)
│   │   └─ On crash: auto-restart (max 3 attempts)
│   ├─ GET /api/status → configured?
│   ├─ Load cached tasks (instant startup)
│   ├─ Fetch fresh tasks (statuses: new, active, in_progress, unknown)
│   └─ Status bar: "TraceAI: 12 tasks"
│
└─ Background refresh (every 5 minutes)
```

### Task Sidebar

Tasks are grouped by status with collapsible headers:

```
▼ In Progress (3)
    🐛 BUG-123: Fix login timeout
    📖 STORY-456: Add dark mode
▼ New (2)
    🐛 BUG-101: Null pointer in checkout
```

Clicking a task triggers investigation immediately.

---

## 8. Directory Structure

```
~/.traceai/
├── config.json              # Platform config (no secrets)
├── cache/
│   └── tasks.json           # Local task cache for fast startup
├── profiles/
│   └── <repo-name>.json     # Project knowledge profiles
├── investigations/
│   └── <id>.json            # Investigation reports
└── logs/
    └── investigation.log    # Audit trail (JSONL)
```

---

## 9. Future Extensions

Planned additions for future versions:

| Extension | Description |
|-----------|-------------|
| **KubernetesSkill** | Pod status, recent restarts, resource limits |
| **AWSSkill** | CloudWatch logs, Lambda errors, ECS task status |
| **CICDSkill** | Pipeline failures, deployment history, build logs |
| **Graph Visualization** | Interactive node diagram in VS Code webview |
| **ML Root Cause Scoring** | Train on past investigations for better ranking |
| **Cross-Investigation Patterns** | Detect recurring issues across investigations |
| **Historical Regression Detection** | Graph overlays comparing past and present |

### Contributing a New Skill

1. Create a file in `src/task_analyzer/skills/`
2. Subclass `BaseSkill` (see Section 4)
3. Register required tools in `TOOL_REGISTRY` if new ones are needed
4. Register the skill in `InvestigationEngine.__init__()`
5. Add tests in `tests/unit/skills/`
