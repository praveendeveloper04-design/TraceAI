# TraceAI — Capability Report

> Date: 2026-03-18
> Branch: `feature/testtrace`
> Commit: `ec7374f`

---

## 1. Initial Product Knowledge Extraction Process

### What Exists Today

TraceAI builds product knowledge through a **three-layer extraction pipeline** that runs once and persists across investigations.

#### Layer 1: Repository Scanning (`knowledge/scanner.py`)

**Trigger**: First-time setup via `traceai setup` or VS Code extension activation.

**What it extracts**:
- Directory tree structure (depth 4, max 5000 files)
- Language distribution (60+ extensions mapped: `.cs` → C#, `.py` → Python, etc.)
- Service detection (Dockerfile, package.json, .csproj, .sln patterns)
- Database model detection (models.py, *.entity.cs, migrations/)
- Key files (README, Dockerfile, .sln, etc.)

**Output**: `ProjectProfile` saved to `~/.traceai/profiles/<repo>.json`

**Limitation**: Lightweight — knows file names and languages but not class structures or code relationships.

#### Layer 2: Workspace Intelligence Index (`workspace_intelligence/index.py`)

**Trigger**: Automatically on first investigation or when repo scan is >24 hours old.

**What it extracts** (via regex parsing of C#, Python, TypeScript):

| Artifact | Count (Current) | Example |
|----------|-----------------|---------|
| Repositories | 2 | Oildroid, PLC |
| Code classes | 29,064 | TripController, FileTransferController |
| Methods | 92,064 | DeleteTrip(), GetCustomers() |
| API routes | 2,107 | GET /api/Trip, POST /api/FileTransfer |
| Class dependencies | — | TripController → ITripService |
| Code-to-table refs | 24,919 | TripController → Trip (dbset) |

**Storage**: SQLite at `~/.traceai/workspace_index.db` (14 tables, 7 indexes)

**Classification logic**: Each class is categorized into a layer:
```
"controller" in name → api_controller
"service" in name   → service
"repository"        → repository
"dbcontext"         → data_access
"model"/"dto"       → model
Has [HttpGet]       → api_controller
Has DbSet<>         → data_access
```

#### Layer 3: Schema Relation Builder (`workspace_intelligence/schema_relation_builder.py`)

**Trigger**: During engine initialization when SQL connector is available.

**What it extracts** (from SQL Server INFORMATION_SCHEMA):

| Artifact | Count (Current) | Source |
|----------|-----------------|--------|
| Database tables | 1,000 | INFORMATION_SCHEMA.TABLES |
| Columns | 15,666 | INFORMATION_SCHEMA.COLUMNS |
| Foreign keys | 362 | INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS |

**Storage**: Same SQLite database (`workspace_index.db`)

### How Knowledge Is Used During Investigation

```
Task: "Delete Trip button not working in BP tenant"
                    ↓
EntityExtractor → ["Trip", "Delete", "button", "BP"]
                    ↓
WorkspaceIndex.find_classes_by_entity("Trip")
  → TripController (api_controller, PLC/PDIUnitManager.API)
  → TripService (service, PLC/PDIUnitManager.Shared)
  → TripRepository (repository, PLC/PDIUnitManager.Shared)
                    ↓
WorkspaceIndex.find_tables_referenced_by_class("TripController")
  → Trip, TripEvent, TripDetail (from DbSet/context refs)
                    ↓
WorkspaceIndex.get_fk_neighbors("Trip")
  → Customer, LoadPlan, Vehicle (via foreign keys)
                    ↓
RankedTableSelector produces:
  Rank 1: Operation.Trip (code reference)
  Rank 2: Operation.TripEvent (class→table mapping)
  Rank 3: Operation.Customer (FK neighbor)
  (No rank 4 fuzzy needed — sufficient high-rank tables found)
```

### What's NOT Extracted Yet

- **Method bodies / call graphs**: Only method names are indexed, not their implementations or call chains
- **Configuration files**: appsettings.json, web.config not parsed
- **Git history**: No commit analysis or blame integration
- **Test coverage**: No mapping of tests to production code
- **Documentation**: README/wiki content not indexed

---

## 2. Workspace Awareness

### What Exists Today

TraceAI is workspace-aware through three configuration files and automatic dependency resolution.

#### Configuration Files

| File | Purpose | Created By |
|------|---------|------------|
| `~/.traceai/workspace_profile.json` | Defines repos, dependencies, services | Manual (user) |
| `~/.traceai/system_map.json` | Infrastructure topology, data flows, tenant→DB mapping | Manual (user) |
| `~/.traceai/config.json` | Active repos, connectors, LLM settings | Setup wizard |

#### Current Workspace Profile

```json
{
  "repos": [
    {"name": "Oildroid", "path": "C:\\...\\Oildroid"},
    {"name": "PLC", "path": "C:\\...\\plc-saas-platform"}
  ],
  "dependencies": {
    "Oildroid": ["PLC"]
  },
  "services": {
    "PDIUnitManager": {"repo": "PLC", "path": "PDIUnitManager/"},
    "OPSharedLib": {"repo": "Oildroid", "path": "OPSharedLib/"}
  }
}
```

#### Current System Map

```json
{
  "services": {
    "Oildroid": {"repo": "Oildroid", "type": "android_app"},
    "ITMServer": {"repo": "PLC", "type": "backend", "path": "PDIUnitManager/"},
    "PDIUnitManager": {"repo": "PLC", "type": "hub_server"}
  },
  "flows": {
    "trip_delete": ["Oildroid", "OVC", "ITMServer", "SQLServer"],
    "device_sync": ["Oildroid", "PDIUnitManager", "SQLServer"]
  },
  "tenant_db_map": {
    "bp": "PLCMain_BP",
    "ck": "PLCMain_CK",
    "rubis": "PLCMain_Rubis"
  }
}
```

#### How Workspace Awareness Works in Practice

1. **Engine initialization**: Loads `workspace_profile.json`, resolves dependencies
2. **Auto-loading**: When investigating an Oildroid task, PLC profiles are automatically loaded
3. **Cross-repo code search**: DeepInvestigator and CodeFlowEngine scan ALL workspace repos
4. **Workspace index**: Both repos indexed in SQLite (29,064 classes total)
5. **Context for Claude**: `[WORKSPACE_ARCHITECTURE]` section injected into LLM prompt with repos, dependency graph, services, DB topology, and index stats
6. **Index context**: `[CODE] Workspace Index Matches` section provides entity-matched classes, routes, and dependencies from ALL repos

#### What Claude Receives

```
# [WORKSPACE_ARCHITECTURE]
## Repositories
- Oildroid (C:\...\Oildroid)
- PLC (C:\...\plc-saas-platform)

## Dependency Graph
- Oildroid -> PLC

## Services
- PDIUnitManager located in PLC repository (PDIUnitManager/)
- OPSharedLib located in Oildroid repository (OPSharedLib/)

## Data Flows
- trip_delete: Oildroid -> OVC -> ITMServer -> SQLServer

## Database Topology
- Tenant 'bp' -> database PLCMain_BP
- Tenant 'ck' -> database PLCMain_CK

## Workspace Index
- 2 repositories indexed, 29,064 classes, 92,064 methods
- 2,107 API routes, 24,919 code-to-table references
- 1,000 database tables, 362 foreign key relationships

# [CODE] Workspace Index Matches
## Api Controllers (10)
- FileTransferController in PLC (PDIUnitManager.API\Controllers\...)
  Dependencies: fileTransferService
- DeviceAppController in PLC (PDIUnitManager.API\Controllers\...)
## API Routes (15)
- GET {owaID} -> FileTransferController
- POST SetNewITMFlag/{deviceId} -> DeviceAppController
```

### What's NOT Workspace-Aware Yet

- **No auto-detection of VS Code workspace folders**: Repos must be manually listed in workspace_profile.json
- **No git remote analysis**: Cannot discover related repos from git submodules or package references
- **No NuGet/npm dependency resolution**: Cross-repo code dependencies not traced through package managers
- **system_map.json is manual**: Data flows and tenant mappings must be hand-written

---

## 3. Helper Script Capabilities

### What Exists Today

#### `traceai_setup.py` — Interactive Setup Script

A standalone Python script that bootstraps the entire TraceAI environment in 5 steps:

| Step | What It Does | Validation |
|------|-------------|------------|
| 1. Directory | Creates `~/.traceai/` structure (cache, profiles, investigations, logs, runtime) | Checks permissions |
| 2. Credentials | Ensures `credentials.json` exists with 0o600 permissions | Shows existing sections |
| 3. Claude API | Detects key from env/registry/file, prompts if missing, tests with real API call | Live API test (claude-sonnet-4-20250514) |
| 4. Azure DevOps | Finds Azure CLI, checks login, acquires token, validates project access | Live ADO API test |
| 5. SQL Database | Shows servers, tests TCP connectivity, prompts credentials, tests SQL connection | Socket + SQLAlchemy test |

**Key features**:
- Multi-source credential detection (env var → Windows registry → credentials.json → keyring)
- Live validation at every step (not just config saving)
- Idempotent — won't overwrite existing working credentials
- Helpful error messages (VPN check, driver install, etc.)

#### `cli/wizard.py` — TUI Setup Wizard

A Rich + questionary-based interactive wizard with 4 steps:

| Step | What It Does |
|------|-------------|
| 1. Repository | Prompts for git repo paths, validates .git exists, runs RepositoryScanner |
| 2. Ticket Source | Lists connectors, prompts for settings, stores credentials in OS keychain |
| 3. Optional Connectors | SQL Database, MCP, etc. — each with setup questions |
| 4. MCP Detection | Auto-detects Claude Code MCP config files |

#### `cli/main.py` — 8 CLI Commands

| Command | Purpose |
|---------|---------|
| `traceai setup` | Run setup wizard |
| `traceai tasks` | List tasks from ticket source |
| `traceai investigate <id>` | Run full AI investigation |
| `traceai status` | Show configuration status |
| `traceai profile [path]` | Show/regenerate project profiles |
| `traceai history` | Show investigation history |
| `traceai validate` | Validate all system components |
| `traceai serve` | Start REST API server |

### What's NOT Implemented Yet

- **No `traceai index` command**: Cannot manually trigger workspace index rebuild from CLI
- **No `traceai workspace init` command**: Cannot auto-generate workspace_profile.json from detected repos
- **No `traceai system-map generate` command**: Cannot auto-discover services and flows
- **No `traceai doctor` command**: No comprehensive health check that tests all components end-to-end
- **No workspace_profile.json auto-generation**: User must manually create the JSON file
- **No system_map.json auto-generation**: Data flows and tenant mappings must be hand-written

---

## Summary: Current State vs Discussed Capabilities

| Capability | Status | What Exists | What's Missing |
|-----------|--------|-------------|----------------|
| **Code class indexing** | ✅ Done | 29,064 classes in SQLite | Method bodies, call graphs |
| **API route extraction** | ✅ Done | 2,107 routes indexed | Route parameter types |
| **Code-to-DB mapping** | ✅ Done | 24,919 references | Stored procedure mapping |
| **FK relationship graph** | ✅ Done | 362 foreign keys | View dependencies |
| **Multi-repo awareness** | ✅ Done | 2 repos, auto-dependency loading | Auto-detection from VS Code |
| **Workspace architecture context** | ✅ Done | 34-line section for Claude | Auto-generated system map |
| **Index-based code evidence** | ✅ Done | 94-line section for Claude | Method-level evidence |
| **Ranked table selection** | ✅ Done | 4-tier ranking, max 12 tables | Query optimization hints |
| **Setup wizard** | ✅ Done | 5-step interactive setup | Auto-workspace-profile generation |
| **CLI commands** | ✅ Done | 8 commands | index, workspace-init, doctor |
| **Persistent knowledge reuse** | ✅ Done | SQLite survives across investigations | Incremental updates |
| **Adaptive investigation** | ✅ Done | Early stop at confidence ≥ 0.7 | Learning from past investigations |
| **Dynamic skill selection** | ✅ Done | Classification-driven | Feedback loop from results |
| **Apply Fixes button** | ✅ Done | Diff view for Claude patches | Auto-apply to workspace |
| **Telemetry logging** | ✅ Done | 6 event types in JSONL | Dashboard/visualization |
