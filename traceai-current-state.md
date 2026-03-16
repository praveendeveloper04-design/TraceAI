# TraceAI — Current System State Report

> Generated: 2026-03-16
> Branch: `feature/testtrace`
> Commit: `8889f0b`
> Codebase: 68 Python files, 18 TypeScript files

---

## Section 1 — Project Overview

### Purpose

TraceAI is an AI-powered developer investigation platform delivered as a VS Code extension with a Python backend. It investigates bugs, incidents, and user stories by automatically analyzing source code repositories, querying SQL databases, fetching ticket context from Azure DevOps, and reasoning about findings using Claude (Anthropic LLM).

### How Users Interact

1. **Sidebar panel** — The TraceAI icon in the VS Code activity bar shows two tree views: "My Tasks" (fetched from Azure DevOps) and "Investigations" (past investigation history).
2. **Click to investigate** — Clicking a task in the sidebar immediately starts an investigation. A dedicated webview tab opens showing animated progress stages.
3. **Command palette** — Users can also trigger investigations via `Ctrl+Shift+P` → "TraceAI: Investigate Task" and enter a task ID manually.
4. **Investigation report** — When complete, the webview displays a structured report with summary, root cause, findings (with confidence scores), recommendations, and affected files.
5. **Re-run / Delete / Cancel** — Context menus on investigation history items allow re-running, deleting, or cancelling investigations.

### Major Capabilities

| Capability | Description |
|---|---|
| **Task ingestion** | Fetches work items from Azure DevOps via Azure CLI authentication |
| **Task classification** | NLP-based categorization into bug, feature, performance, security, data_issue, integration |
| **Multi-layer code analysis** | Traces execution paths through Controller → Service → Repository → Database layers |
| **3-loop deep investigation** | Iterative evidence collection: broad discovery → targeted deepening → verification |
| **SQL intelligence** | Context-aware query generation with cross-database multi-tenant support |
| **Parallel execution** | Concurrent analysis tasks with dependency resolution and error isolation |
| **Evidence-grounded AI reasoning** | Claude produces findings calibrated to actual evidence quality |
| **Investigation graph** | In-memory graph tracking relationships between tickets, files, commits, tables |
| **Root cause ranking** | Heuristic-based hypothesis scoring from graph connectivity and evidence patterns |
| **Security enforcement** | Strict read-only model — no repository writes, no database writes, no code execution |
| **Audit logging** | Every operation logged to `~/.traceai/logs/investigation.log` |

---

## Section 2 — Extension Architecture

### Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                    VS Code Extension (TypeScript)                │
│                                                                 │
│  extension.ts ─── activate() entry point                        │
│       │                                                         │
│       ├── ServerManager ──── Auto-bootstrap Python backend      │
│       ├── StateManager ───── Persist first-run / assignee       │
│       ├── TaskCache ──────── Disk cache at ~/.traceai/cache/    │
│       ├── ApiService ─────── HTTP client (axios) to backend     │
│       ├── PanelManager ───── Webview panels for reports         │
│       ├── TaskTreeProvider ── Sidebar "My Tasks" tree view      │
│       └── InvestigationTreeProvider ── Sidebar history view     │
│                                                                 │
│  Communication: HTTP REST to localhost:7420                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │ HTTP (JSON)
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Python Backend (FastAPI)                      │
│                    Port 7420 on localhost                        │
│                                                                 │
│  api/server.py ─── FastAPI endpoints                            │
│       │                                                         │
│       ├── POST /api/tasks ──────── Fetch tasks from ticket src  │
│       ├── POST /api/investigate ── Run full investigation       │
│       ├── GET  /api/investigations ── List past reports         │
│       ├── GET  /api/health ──────── Health check                │
│       └── GET  /api/status ──────── Configuration status        │
│                                                                 │
│  Investigation Engine (engine.py)                               │
│       │                                                         │
│       ├── TaskClassifier ──────── Categorize task               │
│       ├── InvestigationPlanner ── Extract entities, plan        │
│       ├── ParallelAnalysisEngine ── Run tasks concurrently      │
│       │     ├── CodeFlowAnalysisEngine ── Trace code layers     │
│       │     ├── DeepInvestigator ──────── 3-loop evidence       │
│       │     └── Skills (8 registered) ── Modular analysis       │
│       ├── SQLIntelligence ──────── Smart query generation       │
│       ├── EvidenceAggregator ───── Merge all findings           │
│       ├── RootCauseEngine ──────── Rank hypotheses              │
│       ├── GraphBuilder ─────────── Build relationship graph     │
│       └── Claude LLM ──────────── AI reasoning via LangChain   │
│                                                                 │
│  Security Layer                                                 │
│       ├── SecurityGuard ─── Validate every operation            │
│       ├── RateLimiter ───── Prevent API abuse                   │
│       └── AuditLogger ───── Log all actions                     │
│                                                                 │
│  Connector Layer                                                │
│       ├── AzureDevOpsConnector ── Ticket source (Azure CLI)     │
│       ├── SqlDatabaseConnector ── Read-only SQL queries         │
│       ├── JiraConnector ───────── (registered, not active)      │
│       ├── GitHubIssuesConnector ─ (registered, not active)      │
│       ├── ConfluenceConnector ─── (registered, not active)      │
│       ├── GrafanaConnector ────── (registered, not active)      │
│       ├── SalesforceConnector ─── (registered, not active)      │
│       └── McpConnector ────────── (registered, not active)      │
│                                                                 │
│  Storage                                                        │
│       └── LocalStore ── ~/.traceai/ (config, profiles, reports) │
└─────────────────────────────────────────────────────────────────┘
```

### Request Flow

```
User clicks task in sidebar
    ↓
extension.ts → investigateTask(taskId, taskTitle)
    ↓
PanelManager.openProgress() → webview tab with animated stages
    ↓
ApiService.investigate(taskId) → POST /api/investigate {task_id}
    ↓
server.py → creates InvestigationEngine → engine.investigate(task)
    ↓
TaskClassifier → classify task category and strategy
    ↓
InvestigationPlanner → extract entities, discover schema, build plan
    ↓
ParallelAnalysisEngine → run concurrently:
    ├── CodeFlowAnalysisEngine → trace code layers
    ├── DeepInvestigator → 3-loop evidence collection
    └── 8 Skills → ticket context, repo, DB, cross-repo, etc.
    ↓
SQLIntelligence → generate and execute targeted queries
    ↓
EvidenceAggregator + RootCauseEngine + GraphBuilder
    ↓
Claude LLM reasoning (via LangChain + Anthropic API)
    ↓
InvestigationReport → saved to ~/.traceai/investigations/
    ↓
JSON response → ApiService → PanelManager.showReport() → webview
```

---

## Section 3 — Investigation Pipeline

When a user triggers an investigation, the following steps execute in `engine.py`:

### Step 0: Task Classification

```python
classification = self.task_classifier.classify(title, description, task_type)
```

- Analyzes task title and description using weighted regex pattern matching
- Produces: category (bug/feature/performance/security/data_issue/integration), sub_category, complexity, confidence, investigation_strategy, focus_areas, suggested_skills
- No ML model — pure NLP pattern matching
- Drives which skills run and what queries are generated

### Step 0b: Investigation Planning

```python
planner = InvestigationPlanner()
investigation_plan = planner.plan(title, description, db_connector)
```

- `EntityExtractor` extracts PascalCase identifiers, snake_case, quoted strings, significant words from task text
- `SchemaDiscovery` queries `INFORMATION_SCHEMA.TABLES` to discover all tables in the tenant database
- Fuzzy-matches extracted entities to discovered table names
- `SystemMap` loads `~/.traceai/system_map.json` for service→repo mappings and tenant→database mappings
- Detects tenant from ticket text (e.g., "BP" → `PLCMain_BP`, "CK" → `PLCMain_CK`)
- Generates cross-database SELECT queries for matched tables

### Step 1: Parallel Analysis

```python
parallel = ParallelAnalysisEngine()
parallel.add_task("code_flow_analysis", ...)
parallel.add_task("deep_investigation", ...)
parallel.add_task("skill_repo_analysis", ...)
# ... 9 tasks total
analysis_result = await parallel.execute()
```

All tasks run concurrently in a single wave:

| Task | Engine | What It Does |
|---|---|---|
| `code_flow_analysis` | CodeFlowAnalysisEngine | Scans up to 200 files, parses C#/Python/TS, builds LayerMap with nodes, edges, execution flows |
| `deep_investigation` | DeepInvestigator | 3-loop iterative evidence collection (see below) |
| `skill_repo_analysis` | RepoAnalysisSkill | Matches task keywords to project key files |
| `skill_ticket_context` | TicketContextSkill | Searches for related tickets by keywords |
| `skill_database_analysis` | DatabaseAnalysisSkill | Queries database for task-related data |
| `skill_cross_repo_analysis` | CrossRepoAnalysisSkill | Searches dependent repositories |
| `skill_database_schema` | DatabaseSchemaSkill | Discovers and caches database schema |
| `skill_code_analysis` | CodeAnalysisSkill | Extracts SQL table references from code |
| `skill_sql_query` | SQLQuerySkill | Executes planner-generated queries |

### Step 2: SQL Intelligence

```python
queries = self.sql_intelligence.generate_queries(tables, schema_info, task_category, entities, code_tables)
sql_results = self.sql_intelligence.execute_queries(db_connector, queries)
```

- Generates 5 query types: recent_records, error_states, data_integrity, aggregation, timeline
- Prioritizes code-discovered tables over schema-discovered tables
- All queries validated through SecurityGuard before execution
- Executes up to 12 queries with row limits and timeouts

### Step 3: Evidence Aggregation

```python
aggregator = EvidenceAggregator()
evidence = aggregator.aggregate(skill_results)
hypotheses = root_engine.analyze(graph.export(), evidence)
graph_builder.build(graph, task.id, evidence, hypotheses)
```

- Merges findings from all skills into unified evidence dict (files, commits, contributors, errors, log_entries, database_anomalies, related_tasks)
- RootCauseEngine scores hypotheses using heuristics: recent commits (0.75), error patterns (0.6), database anomalies (0.4), graph connectivity (0.3+)
- GraphBuilder adds nodes and edges to the investigation graph

### Step 4: Context Building

```python
context = await self._build_context(task, evidence, aggregator, graph, skill_results, investigation_plan, deep_evidence, layer_map, classification, sql_results)
```

Assembles a rich text context string with tagged evidence sections:

- `[TICKET]` — Task title, description, comments
- `[CODE]` — Code flow analysis, source code snippets, application layers
- `[SQL]` — Database query results with observational-only caveat
- `[SCHEMA]` — Database schema discovery
- `[ARCHITECTURE]` — System map, workspace profile, investigation plan
- Evidence quality assessment (tells Claude what evidence is available/missing)

### Step 5: LLM Reasoning

```python
result = await self._run_investigation(context, tools)
self._parse_result(result, report)
```

- Sends system prompt + context to Claude via LangChain `ChatAnthropic`
- System prompt enforces: never fabricate evidence, confidence calibration rules, separate verified findings from hypotheses
- If tool calling fails (PDI AI Gateway limitation), retries without tools
- If LLM fails entirely, produces partial report from skill evidence
- Parses JSON output into structured `InvestigationReport`

### Step 6: Report Generation

- Attaches investigation graph, root cause hypotheses, evidence summary to report
- Saves report to `~/.traceai/investigations/<uuid>.json`
- Returns JSON to VS Code extension for webview rendering

---

## Section 4 — Repository Analysis

### File Scanning Strategy

The system uses three independent file scanning mechanisms:

#### 1. CodeFlowAnalysisEngine (`code_flow_engine.py`)

- Walks repository directories using `os.walk()`
- Skips: `node_modules`, `bin`, `obj`, `dist`, `build`, `__pycache__`, `.git`, `packages`, `TestResults`, `.vs`, `.idea`, `.vscode`, `wwwroot`, `migrations`
- Scans file extensions: `.cs`, `.py`, `.ts`, `.js`
- Matches filenames against entity patterns (case-insensitive substring match)
- Limit: 200 files per investigation
- For each matched file, parses classes, methods, dependencies, DB table references

#### 2. DeepInvestigator (`deep_investigator.py`)

- **Loop 1 (Broad)**: Walks repos matching entity patterns against filenames. Extensions: `.cs`, `.py`, `.ts`, `.js`, `.java`, `.sql`, `.xml`, `.json`. Limit: 100 files.
- **Loop 2 (Targeted)**: Reads found files, extracts code patterns (Controller/Service/Repository classes), searches for action+entity compound patterns (e.g., "DeleteTrip"). Limit: 30 files read, 50 search results.
- **Loop 3 (Verification)**: If evidence quality < 50, does a deep grep through all code files for any entity mention. Limit: 150 files.

#### 3. CodeAnalysisSkill (`code_analysis.py`)

- Searches repos for files matching entity names
- Reads file content and extracts code flows (class patterns) and SQL table references
- Extensions: `.cs`, `.py`, `.ts`, `.js`, `.java`, `.go`, `.rs`, `.rb`, `.sql`, `.xml`, `.json`, `.yaml`, `.yml`

### Symbol and Dependency Extraction

**C# parsing** (primary language):
- Class declarations: `public class TripController : ControllerBase`
- Constructor injection: `private readonly ITripService _tripService;`
- HTTP attributes: `[HttpGet("api/trips")]`, `[Route("...")]`
- Methods: public/private/async method signatures
- DbSet properties: `DbSet<Trip> Trips`
- Table references: `_context.Trips`, `FROM [Trips]`, `JOIN [Orders]`

**Python parsing**:
- Class declarations with base classes
- Route decorators: `@app.get("/api/trips")`
- SQLAlchemy models: `__tablename__ = "trips"`

**TypeScript parsing**:
- Class declarations with export
- Layer classification by naming convention

### How Relevant Files Are Selected

1. **Entity extraction** from task title/description (PascalCase, snake_case, quoted strings, significant words)
2. **Filename matching** — entity substring match against filenames (case-insensitive)
3. **Content matching** — entity substring match against file content (Loop 2 and 3)
4. **Layer classification** — classes categorized as Controller/Service/Repository/Model based on naming patterns and base classes

---

## Section 5 — SQL Handling

### Database Access Configuration

- Connection string stored in OS keychain (Windows Credential Manager / macOS Keychain / Linux Secret Service)
- Fallback: `~/.traceai/credentials.json` → `{"sql_database": {"connection_string": "..."}}`
- SQLAlchemy engine with `pool_size=2, max_overflow=0, pool_pre_ping=True`
- Connection timeout: 30 seconds

### How SQL Queries Are Generated

Four independent query generation paths:

#### 1. InvestigationPlanner (`planner.py`)

- Matches extracted entities to discovered table names via fuzzy matching
- Generates `SELECT TOP 20 * FROM [TenantDB].[Schema].[Table] ORDER BY 1 DESC`
- Cross-database syntax: `[PLCMain_CK].[Operation].[Trip]`

#### 2. SQLIntelligence (`sql_intelligence.py`)

- Generates 5 query types based on task classification:
  - **Recent records**: `SELECT TOP 10 * FROM {table} ORDER BY 1 DESC`
  - **Error states**: `SELECT TOP 20 * FROM {table} WHERE [Status] IN ('Error', 'Failed', ...) ORDER BY 1 DESC`
  - **Data integrity**: `SELECT TOP 10 * FROM {table} WHERE [Name] IS NULL OR [Status] IS NULL`
  - **Aggregation**: `SELECT [Status], COUNT(*) FROM {table} GROUP BY [Status] ORDER BY cnt DESC`
  - **Timeline**: `SELECT TOP 20 * FROM {table} WHERE [CreatedAt] >= DATEADD(day, -7, GETDATE()) ORDER BY [CreatedAt] DESC`
- Discovers foreign key relationships via `INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS`
- Generates JOIN queries for related tables

#### 3. DeepInvestigator (`deep_investigator.py`)

- Discovers schema via `INFORMATION_SCHEMA.TABLES` and `INFORMATION_SCHEMA.COLUMNS`
- Queries sample rows: `SELECT TOP 10 * FROM [TenantDB].[Schema].[Table] ORDER BY 1 DESC`
- Extracts table names from code and queries them if they exist in schema

#### 4. Code-discovered tables

- `CodeAnalysisSkill` extracts table names from code patterns (DbSet, context.Table, FROM/JOIN)
- `InvestigationPlanner.build_queries_from_code_tables()` validates against schema and generates queries

### Schema Discovery

- Queries `{TenantDB}.INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'`
- Returns `Schema.Table` format (e.g., `Operation.Trip`, `dbo.Customer`)
- Column metadata from `INFORMATION_SCHEMA.COLUMNS` (name, data type, ordinal position)
- Schema cached to `~/.traceai/db_profiles/<database>.json`

### Security Enforcement

- **Every** SQL query passes through `SecurityGuard.validate_sql_query()` before execution
- Schema inspection queries use `allow_schema_inspection=True` (permits INFORMATION_SCHEMA only)
- Data queries use strict mode (blocks all system objects)
- Blocked keywords: INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, EXEC, EXECUTE, GRANT, REVOKE, MERGE, CALL, REPLACE, LOAD, RENAME
- Blocked objects: SYS.*, SYSOBJECTS, XP_*, SP_EXECUTESQL, OPENROWSET, OPENQUERY, MASTER.., MSDB.., TEMPDB..
- Comment stripping prevents bypass via `--` or `/* */`
- Compound statement rejection (semicolons)
- First keyword must be SELECT or WITH
- Row limits: `SET ROWCOUNT` applied before every query
- Lock timeout: `SET LOCK_TIMEOUT` applied before every query

### How Query Results Are Used

- Sample rows included in LLM context as `[SQL]` tagged evidence
- Explicit caveat: "SQL data shows current state only. It does NOT prove causation."
- Claude instructed to never assign confidence > 0.5 without code-level evidence
- Results stored in investigation report as `evidence_summary`

---

## Section 6 — Configuration System

### Configuration Sources

| Source | Location | Purpose |
|---|---|---|
| **Platform config** | `~/.traceai/config.json` | Ticket source, connectors, repositories, LLM settings, mode |
| **Credentials** | OS keychain → `~/.traceai/credentials.json` → config.settings | API keys, connection strings (never in config.json) |
| **Workspace profile** | `~/.traceai/workspace_profile.json` | Multi-repo definitions, dependencies, services |
| **System map** | `~/.traceai/system_map.json` | Service→repo mappings, tenant→database mappings, data flows |
| **VS Code settings** | `settings.json` → `traceai.*` | Server port (7420), auto-start, default assignee |
| **Environment variables** | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | LLM API credentials (resolved from env → Windows registry → credentials.json → keyring) |
| **Extension state** | VS Code globalState | First-run flag, setup complete flag, assignee preference |

### Platform Config Structure (`config.json`)

```json
{
    "config_version": "1.0",
    "mode": "safe",
    "ticket_source": {
        "name": "azure_devops",
        "connector_type": "azure_devops",
        "enabled": true,
        "settings": {
            "organization": "PDIDevWEUR",
            "project": "oildroid-itm"
        }
    },
    "connectors": [
        {
            "name": "sql_database",
            "connector_type": "sql_database",
            "enabled": true,
            "settings": {"database_name": "MT Dev"}
        }
    ],
    "repositories": ["C:\\Users\\...\\Oildroid"],
    "llm_model": "claude-sonnet-4-20250514",
    "llm_temperature": 0.1,
    "llm_max_tokens": 4096,
    "background_refresh": true
}
```

### Config Loading Flow

1. `LocalStore.load_config()` reads `~/.traceai/config.json`
2. Checks `config_version` — runs `migrate_config()` if outdated
3. Validates via Pydantic `PlatformConfig.model_validate(data)`
4. Credentials resolved separately via `CredentialManager` (3-tier: keyring → file → settings)
5. Anthropic env vars synced from Windows registry on server startup

---

## Section 7 — Workspace Context

### Scope Determination

The extension determines project scope through three mechanisms:

#### 1. Configured Repositories (`config.json`)

- `config.repositories` lists absolute paths to repository roots
- Each repository gets a `ProjectProfile` (scanned or cached)
- Profile contains: repo_name, repo_path, key_files, languages, frameworks, context_summary

#### 2. Workspace Profile (`workspace_profile.json`)

- Defines multiple repositories and their relationships
- Structure:
  ```json
  {
      "repos": [
          {"name": "Oildroid", "path": "C:/dev/Oildroid"},
          {"name": "PLC", "path": "C:/dev/PLC"}
      ],
      "dependencies": {"Oildroid": ["PLC"]},
      "services": {"PDIUnitManager": {"repo": "PLC", "path": "PDIUnitManager/"}}
  }
  ```
- When investigating a task in Oildroid, PLC profiles are automatically loaded as dependencies
- `WorkspaceScanner.get_dependency_profiles()` resolves the dependency chain

#### 3. System Map (`system_map.json`)

- Maps services to repositories and data flows
- Contains tenant→database mappings (e.g., `"bp": "PLCMain_BP"`, `"ck": "PLCMain_CK"`)
- No domain-specific keywords — only infrastructure topology

### Multi-Repository Support

- **Yes**, multiple repositories are supported via workspace_profile.json
- `CrossRepoAnalysisSkill` searches dependent repos for related services
- `CodeFlowAnalysisEngine` scans all repo paths provided
- `DeepInvestigator` walks all repo paths for entity matching
- Dependency direction is explicit: Oildroid depends on PLC, not vice versa

### Current Workspace Handling

- The extension does NOT automatically detect VS Code workspace folders
- Repository paths must be manually configured in `config.json` or `workspace_profile.json`
- The `RepositoryScanner` scans a single repo root and produces a `ProjectProfile`
- Profiles are cached to `~/.traceai/profiles/<repo-name>.json`

---

## Section 8 — Data Storage / Caching

### Storage Layout

```
~/.traceai/
├── config.json                    # Platform configuration (no secrets)
├── credentials.json               # Fallback credential storage
├── workspace_profile.json         # Multi-repo definitions
├── system_map.json                # Service topology and tenant mappings
├── profiles/                      # Project knowledge profiles
│   └── Oildroid.json              # Cached repo scan results
├── investigations/                # Investigation reports
│   └── <uuid>.json                # Full report with findings, graph, evidence
├── cache/                         # Ephemeral cache
│   └── tasks.json                 # Local task cache for instant startup
├── db_profiles/                   # Database schema cache
│   └── <database>.json            # Cached table/column metadata
├── logs/                          # Audit logs
│   └── investigation.log          # JSONL structured audit trail
└── runtime/                       # Auto-bootstrapped Python environment
    └── venv/                      # Virtual environment
```

### What Is Cached and Why

| Cache | Format | TTL | Purpose |
|---|---|---|---|
| **Task cache** | `cache/tasks.json` | 5 minutes | Instant task display on VS Code startup before API responds |
| **Project profiles** | `profiles/<repo>.json` | Indefinite | Avoid re-scanning large repositories on every investigation |
| **DB schema** | `db_profiles/<db>.json` | Indefinite | Avoid re-querying INFORMATION_SCHEMA on every investigation |
| **Investigation reports** | `investigations/<uuid>.json` | Indefinite | Persistent history for re-viewing past investigations |
| **Generic cache** | `cache/<key>.json` | Configurable TTL | General-purpose ephemeral cache with expiry |

### Cache Behavior

- **Task cache**: Written after every successful task fetch. Read on startup before API call completes. Stale check: 5 minutes.
- **Profile cache**: Written after `RepositoryScanner.scan()`. Read before scanning. No automatic invalidation — manual re-scan required.
- **Schema cache**: Written by `DatabaseSchemaSkill`. Read before querying INFORMATION_SCHEMA. No automatic invalidation.
- **Investigation reports**: Written after every completed investigation. Never automatically deleted. User can delete via UI.

---

## Section 9 — Performance Model

### Execution Model

The investigation pipeline uses a **hybrid sequential + parallel** execution model:

```
Sequential: Classification → Planning → [Parallel Block] → SQL Intelligence → Aggregation → LLM
                                              │
Parallel:                          ┌──────────┼──────────┐
                                   │          │          │
                            CodeFlow    DeepInvestigator  Skills (8)
                            (0.3s)       (40s)           (1-10s each)
```

### Parallel Execution Details

- `ParallelAnalysisEngine` uses `asyncio.gather()` for concurrent execution
- Tasks are organized into waves based on dependencies (currently all tasks run in wave 1)
- Each task has an individual timeout (default 30s, deep investigation 60s)
- Error isolation: one task failure does not affect others
- Thread pool (`ThreadPoolExecutor`, 4 workers) available for CPU-bound tasks

### Timing Breakdown (Typical Investigation)

| Phase | Duration | Notes |
|---|---|---|
| Task classification | < 0.1s | Pure regex, no I/O |
| Investigation planning | 1-2s | Schema discovery query |
| Parallel analysis | 40-50s | Dominated by DeepInvestigator (schema + table queries) |
| SQL Intelligence | 15-20s | 12 queries executed sequentially |
| Evidence aggregation | < 0.1s | In-memory only |
| Context building | 1-2s | Connector context fetching |
| LLM reasoning | 60-80s | Claude API call (depends on context size) |
| **Total** | **80-150s** | Varies by task complexity and database size |

### Background Tasks

- **Task refresh**: `setInterval` every 5 minutes in the extension
- **Server health monitoring**: Extension polls `/api/health` during startup
- **Crash recovery**: `ServerManager` auto-restarts the Python backend up to 3 times

### Progress Reporting

- Extension shows animated progress stages in the notification bar (`vscode.window.withProgress`)
- Webview panel shows stage-by-stage progress with icons (pending → running → completed)
- Backend emits progress callbacks: `loading_ticket`, `classifying`, `parallel_analysis`, `deep_investigation`, `sql_intelligence`, `evidence_aggregation`, `building_graph`, `building_context`, `ai_reasoning`, `generating_report`
- API server tracks investigation state via `InvestigationState` / `investigation_registry`

### Rate Limiting

- Connector-level rate limiting prevents API abuse:
  - Azure DevOps: 30s minimum between calls
  - SQL Database: 5s minimum between calls
  - Grafana/MCP: 10s minimum between calls
- Connector-level timeouts: repo 10s, tickets 20s, database 30s, logs 15s

---

## Section 10 — Current Limitations

### 1. File Scanning Is Filename-Based

- Primary file discovery matches entity substrings against filenames
- A file named `Utils.cs` containing critical trip logic would be missed if "Trip" is not in the filename
- No AST-level parsing — regex-based class/method extraction misses complex patterns
- No call graph analysis — dependency injection is resolved by naming convention only

### 2. Entity Extraction Is Keyword-Based

- `EntityExtractor` uses regex patterns (PascalCase, snake_case, significant words)
- Cannot understand semantic meaning — "inability to open dropdown" extracts "inability", "open", "dropdown" as separate entities
- Stop word filtering is static (110+ words) — domain-specific noise words are not filtered
- No synonym resolution — "Trip" and "Journey" are treated as unrelated entities

### 3. Schema Discovery Matches Too Broadly

- Fuzzy matching entities to table names produces false positives
- Entity "prod" matches tables containing "prod" anywhere: `mtprodexistingcustomers`, `ProductMappingsCK`, `stprodcustomersexport`
- No relevance ranking — all matches are treated equally
- 95 tables matched for a single investigation is excessive

### 4. SQL Queries Are Template-Based

- All generated queries follow fixed templates (`SELECT TOP N * FROM ... ORDER BY 1 DESC`)
- No understanding of table relationships when generating queries
- No WHERE clause filtering based on task context (e.g., filtering by tenant, date range, or entity ID)
- Foreign key discovery is attempted but not used to generate meaningful JOIN queries

### 5. No Persistent Project Knowledge

- Repository profiles are scanned once and cached, but contain only file lists and framework detection
- No persistent understanding of: class hierarchies, method signatures, API endpoints, database entity relationships
- Every investigation re-discovers the same code patterns from scratch
- No learning from past investigations

### 6. Single LLM Call Architecture

- The entire investigation context is sent to Claude in a single prompt
- Context can be very large (deep evidence + code flows + SQL results + schema + architecture)
- No iterative LLM reasoning — Claude cannot ask for more evidence
- Tool calling fails on the PDI AI Gateway, forcing retry without tools every time

### 7. No Incremental Analysis

- Every investigation starts from scratch — no delta analysis
- Cannot detect "what changed since last investigation"
- No git diff integration for recent changes
- No comparison between current state and previous investigation findings

### 8. Limited Progress Visibility

- Extension shows animated stages but they are time-based animations, not real progress
- The actual backend progress (which loop, which table, which skill) is logged but not streamed to the UI
- SSE streaming endpoint exists but is not used by the current extension code (falls back to blocking POST)

### 9. Workspace Configuration Is Manual

- Repository paths, workspace profiles, and system maps must be manually configured
- No automatic detection of VS Code workspace folders
- No automatic discovery of related repositories from git remotes or package references
- Adding a new repository requires editing JSON files

### 10. No Test Coverage

- No unit tests for any component
- No integration tests for the investigation pipeline
- Security guard validation is tested ad-hoc via inline scripts, not a test suite
- No CI/CD pipeline

### 11. DeepInvestigator Dominates Execution Time

- The 3-loop deep investigation takes 40-50 seconds (60-70% of parallel phase)
- Schema discovery queries 500+ tables, then fuzzy-matches and queries 95 tables
- Other parallel tasks (CodeFlowAnalysis, skills) complete in < 10 seconds but must wait
- SQL Intelligence runs sequentially after the parallel phase, adding another 15-20 seconds

### 12. No Streaming Response

- The investigation API endpoint blocks for the entire duration (80-150 seconds)
- The extension sets a 10-minute timeout but shows no real-time progress from the backend
- The SSE streaming endpoint (`/api/investigate/{taskId}/stream`) exists but the extension's `investigateWithProgress()` method is not called from the investigation flow

---

*End of Current System State Report*
