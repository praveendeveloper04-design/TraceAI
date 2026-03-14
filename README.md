# TraceAI

**AI-Powered Developer Investigation Platform**

TraceAI helps developers investigate bugs, incidents, user stories, and tasks using AI reasoning powered by LangChain and Claude. It connects to your existing tools — ticket systems, documentation, databases, logs — and produces structured investigation reports.

> *TraceAI — AI-powered developer investigation platform that helps engineers analyze tasks, repositories, and engineering systems using AI agents.*

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          INTERFACE LAYER                                    │
│                                                                             │
│   ┌──────────────┐  ┌──────────┐  ┌─────────────┐  ┌──────────────────┐   │
│   │  VS Code      │  │  CLI     │  │  Streamlit  │  │  Chatbot         │   │
│   │  Extension    │  │  (Typer) │  │  (Future)   │  │  (Future)        │   │
│   └──────┬───────┘  └────┬─────┘  └──────┬──────┘  └────────┬─────────┘   │
│          │               │               │                   │             │
└──────────┼───────────────┼───────────────┼───────────────────┼─────────────┘
           │               │               │                   │
┌──────────┼───────────────┼───────────────┼───────────────────┼─────────────┐
│          ▼               ▼               ▼                   ▼             │
│                    API / PROTOCOL LAYER                                     │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────┐     │
│   │  FastAPI Server (REST + WebSocket)          Port 7420            │     │
│   │  ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────────┐ │     │
│   │  │ /tasks   │ │/investig.│ │ /profiles  │ │ /status          │ │     │
│   │  └──────────┘ └──────────┘ └────────────┘ └──────────────────┘ │     │
│   └──────────────────────────┬───────────────────────────────────────┘     │
│                              │                                             │
│   Future: Webhooks ──────────┤                                             │
│                              │                                             │
└──────────────────────────────┼─────────────────────────────────────────────┘
                               │
┌──────────────────────────────┼─────────────────────────────────────────────┐
│                              ▼                                             │
│                     CORE ENGINE LAYER                                      │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────┐     │
│   │                  Investigation Engine                             │     │
│   │                                                                   │     │
│   │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │     │
│   │  │  LangChain  │  │  Tool Router │  │  Report Generator      │  │     │
│   │  │  Orchestr.  │──│  (dynamic    │──│  (structured JSON →    │  │     │
│   │  │  (Claude)   │  │   tool call) │  │   Markdown/HTML)       │  │     │
│   │  └─────────────┘  └──────────────┘  └────────────────────────┘  │     │
│   │                                                                   │     │
│   │  Workflow: Ingest → Context Build → Reason → Tool Use → Report   │     │
│   └──────────────────────────────────────────────────────────────────┘     │
│                                                                             │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
           ▼                   ▼                   ▼
┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────────────────┐
│ KNOWLEDGE LAYER │ │ CONNECTOR LAYER  │ │ SECURITY LAYER                   │
│                 │ │                  │ │                                  │
│ ┌─────────────┐│ │ ┌──────────────┐ │ │ ┌──────────────────────────────┐ │
│ │  Repository ││ │ │ BaseConnector│ │ │ │  OS Keychain (keyring)       │ │
│ │  Scanner    ││ │ │  Interface   │ │ │ │  ┌────────┐ ┌────────────┐  │ │
│ │             ││ │ └──────┬───────┘ │ │ │  │ macOS  │ │ Windows    │  │ │
│ │ ┌─────────┐││ │        │         │ │ │  │Keychain│ │ Cred.Locker│  │ │
│ │ │ Project │││ │ ┌──────┴───────┐ │ │ │  └────────┘ └────────────┘  │ │
│ │ │ Profile │││ │ │  Ticket      │ │ │ │  ┌────────────────────────┐  │ │
│ │ │ (JSON)  │││ │ │  Sources     │ │ │ │  │ Linux Secret Service   │  │ │
│ │ └─────────┘││ │ │ ┌──────────┐ │ │ │ │  └────────────────────────┘  │ │
│ │             ││ │ │ │Azure DO │ │ │ │ └──────────────────────────────┘ │
│ │ Future:     ││ │ │ │Jira     │ │ │ │                                  │
│ │ ┌─────────┐││ │ │ │GitHub   │ │ │ │ No secrets on disk. Ever.        │
│ │ │ RAG     │││ │ │ └──────────┘ │ │ │                                  │
│ │ │ Engine  │││ │ │              │ │ └──────────────────────────────────┘
│ │ └─────────┘││ │ │  Context     │ │
│ └─────────────┘│ │ │  Providers   │ │ ┌──────────────────────────────────┐
│                 │ │ │ ┌──────────┐ │ │ │ STORAGE LAYER                    │
└─────────────────┘ │ │ │Confluenc│ │ │ │                                  │
                    │ │ │Salesforc│ │ │ │ ~/.traceai/                      │
                    │ │ │SQL DB   │ │ │ │ ├── config.json                  │
                    │ │ │MCP      │ │ │ │ ├── profiles/                    │
                    │ │ │Grafana  │ │ │ │ │   └── <repo>.json              │
                    │ │ └──────────┘ │ │ │ ├── investigations/             │
                    │ └──────────────┘ │ │ │   └── <id>.json               │
                    │                  │ │ └── cache/                       │
                    │  Plugin System:  │ │                                  │
                    │  register() →    │ │ Future: SQLite, Data Lake        │
                    │  create() →      │ │                                  │
                    │  use             │ └──────────────────────────────────┘
                    └──────────────────┘
```

---

## Quick Start

### 1. Install

```bash
pip install -e ".[all]"
```

### 2. Configure

```bash
traceai setup
```

This launches the interactive setup wizard that walks you through:
- **Step 1**: Git repository scanning
- **Step 2**: Ticket source configuration (Azure DevOps / Jira / GitHub)
- **Step 3**: Optional connectors (Confluence, SQL, Grafana, etc.)
- **Step 4**: MCP detection

### 3. Investigate

```bash
# List your tasks
traceai tasks --assigned-to "your.name@email.com"

# Investigate a specific task
traceai investigate 12345

# View investigation history
traceai history
```

### 4. VS Code Extension

Install the extension from `vscode-extension/`, then:
1. Click the TraceAI icon in the activity bar
2. Fetch your assigned tasks
3. Click a task to start an AI investigation
4. View the structured report in a webview panel

---

## Project Structure

```
TraceAI/
├── pyproject.toml                    # Python package configuration
├── README.md                         # This file
│
├── src/task_analyzer/                # Main Python package
│   ├── __init__.py
│   │
│   ├── models/                       # Pydantic data models
│   │   ├── __init__.py
│   │   └── schemas.py                # Task, Report, Config, Profile models
│   │
│   ├── connectors/                   # Plugin-style connector system
│   │   ├── __init__.py               # Registry + all connector imports
│   │   ├── base/
│   │   │   ├── connector.py          # BaseConnector abstract class
│   │   │   └── registry.py           # ConnectorRegistry
│   │   ├── azure_devops/             # Azure DevOps work items
│   │   ├── jira/                     # Jira issues
│   │   ├── github_issues/            # GitHub issues
│   │   ├── confluence/               # Confluence wiki search
│   │   ├── salesforce/               # Salesforce cases
│   │   ├── sql_database/             # Read-only SQL queries
│   │   ├── mcp/                      # Model Context Protocol
│   │   └── grafana/                  # Grafana dashboards & logs
│   │
│   ├── knowledge/                    # Repository analysis
│   │   ├── __init__.py
│   │   └── scanner.py                # RepositoryScanner → ProjectProfile
│   │
│   ├── investigation/                # AI reasoning engine
│   │   ├── __init__.py
│   │   └── engine.py                 # InvestigationEngine (LangChain + Claude)
│   │
│   ├── security/                     # Credential management
│   │   ├── __init__.py
│   │   └── credential_manager.py     # OS keychain wrapper
│   │
│   ├── storage/                      # Local persistence
│   │   ├── __init__.py
│   │   └── local_store.py            # JSON file storage
│   │
│   ├── cli/                          # Command-line interface
│   │   ├── __init__.py
│   │   ├── main.py                   # Typer CLI commands
│   │   └── wizard.py                 # Interactive setup wizard
│   │
│   ├── api/                          # REST API server
│   │   ├── __init__.py
│   │   └── server.py                 # FastAPI endpoints
│   │
│   └── utils/                        # Shared utilities
│       ├── __init__.py
│       └── logging.py                # Structured logging config
│
├── vscode-extension/                 # VS Code extension
│   ├── package.json                  # Extension manifest
│   ├── tsconfig.json
│   └── src/
│       ├── extension.ts              # Extension entry point
│       ├── services/
│       │   └── apiService.ts         # API client
│       ├── providers/
│       │   ├── taskTreeProvider.ts    # Task sidebar tree
│       │   └── investigationTreeProvider.ts
│       └── views/
│           └── reportWebview.ts      # Investigation report renderer
│
├── tests/                            # Test suite
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docs/                             # Documentation
│   ├── architecture/
│   ├── guides/
│   └── api/
│
└── configs/                          # Example configurations
```

---

## Module Explanations

### Models (`models/schemas.py`)

Canonical Pydantic models that define the data shapes used across the entire platform:

| Model | Purpose |
|-------|---------|
| `Task` | Normalized task from any ticket source |
| `ProjectProfile` | Repository knowledge profile |
| `InvestigationReport` | Structured AI investigation output |
| `InvestigationFinding` | Individual finding with confidence score |
| `PlatformConfig` | Top-level configuration (no secrets) |
| `ConnectorConfig` | Per-connector configuration |

### Connectors (`connectors/`)

Plugin-style architecture where each connector implements `BaseConnector`:

```python
class BaseConnector(ABC):
    async def validate_connection(self) -> bool: ...
    async def fetch_tasks(self, ...) -> list[Task]: ...
    async def get_task_detail(self, task_id: str) -> Task | None: ...
    async def search(self, query: str, ...) -> list[dict]: ...
    async def get_context(self, task: Task) -> str: ...
```

**Adding a new connector:**
1. Create a new directory under `connectors/`
2. Implement `BaseConnector` in `connector.py`
3. Register it in `connectors/__init__.py`

### Investigation Engine (`investigation/engine.py`)

The core AI system using LangChain:

```
Task → Context Builder → LangChain Agent → Claude → Tool Calls → Report
         │                                              │
         ├── Project Profile                            ├── Search connectors
         ├── Task details + comments                    ├── Query databases
         └── Connector context                          └── Fetch logs
```

The engine dynamically creates LangChain tools from configured connectors, so Claude can decide which tools to invoke during reasoning.

### Security (`security/credential_manager.py`)

All credentials are stored in the OS keychain via the `keyring` library:
- **macOS**: Keychain
- **Windows**: Windows Credential Locker
- **Linux**: Secret Service (GNOME Keyring / KWallet)

No secret is ever written to disk in plaintext.

### Knowledge (`knowledge/scanner.py`)

Scans Git repositories to build lightweight project profiles:
- Language detection by file extension
- Service/module boundary detection
- Database model discovery
- Directory tree generation

Profiles are cached locally so the AI doesn't rescan on every investigation.

---

## Setup Wizard Flow

```
┌─────────────────────────────────────────────────────────┐
│                    SETUP WIZARD                          │
│                                                          │
│  Step 1: Repository                                      │
│  ├── Enter repo path                                     │
│  ├── Validate .git exists                                │
│  ├── Scan → generate ProjectProfile                      │
│  └── Display scan results                                │
│                                                          │
│  Step 2: Ticket Source (REQUIRED)                         │
│  ├── Select: Azure DevOps / Jira / GitHub                │
│  ├── Enter configuration (org, project, etc.)            │
│  ├── Enter credentials (stored in OS keychain)           │
│  └── Display security notice                             │
│                                                          │
│  Step 3: Optional Connectors                             │
│  ├── For each: Confluence, Salesforce, SQL, Grafana      │
│  │   ├── "Configure X?" (yes/no)                         │
│  │   ├── If yes: enter settings + credentials            │
│  │   └── If no: skip                                     │
│  └── All optional, all skippable                         │
│                                                          │
│  Step 4: MCP Detection                                   │
│  ├── "Do you have MCP configured locally?"               │
│  ├── If yes: auto-detect from known paths                │
│  └── If no: offer manual configuration                   │
│                                                          │
│  Summary: Display configuration table                    │
│  Save: config.json (no secrets)                          │
└─────────────────────────────────────────────────────────┘
```

---

## Connector Plugin Design

```
                    ┌──────────────────┐
                    │  ConnectorRegistry│
                    │                  │
                    │  register(cls)   │
                    │  create(config)  │
                    │  get_instance()  │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌──────────────┐ ┌──────────┐ ┌──────────────┐
     │ BaseConnector│ │          │ │              │
     │ (Abstract)   │ │  Ticket  │ │   Context    │
     │              │ │  Sources │ │   Providers  │
     │ validate()   │ │          │ │              │
     │ fetch_tasks()│ │ AzureDO  │ │ Confluence   │
     │ get_detail() │ │ Jira     │ │ Salesforce   │
     │ search()     │ │ GitHub   │ │ SQL Database │
     │ get_context()│ │          │ │ MCP          │
     │ disconnect() │ │          │ │ Grafana      │
     └──────────────┘ └──────────┘ └──────────────┘
```

Each connector:
- Implements the same interface
- Manages its own HTTP client lifecycle
- Retrieves credentials from OS keychain
- Provides `get_setup_questions()` for the wizard
- Is registered at import time

---

## LangChain Investigation Workflow

```
┌─────────┐     ┌──────────────┐     ┌─────────────────┐
│  Task    │────▶│  Context     │────▶│  LangChain      │
│  Input   │     │  Builder     │     │  Agent          │
└─────────┘     └──────────────┘     └────────┬────────┘
                                              │
                     ┌────────────────────────┤
                     │                        │
                     ▼                        ▼
              ┌──────────────┐        ┌──────────────┐
              │  Claude LLM  │◀──────▶│  Tool Router │
              │  (Reasoning) │        │              │
              └──────┬───────┘        │ search_jira  │
                     │                │ search_sql   │
                     │                │ context_graf │
                     ▼                └──────────────┘
              ┌──────────────┐
              │  Report      │
              │  Generator   │
              │              │
              │  JSON → MD   │
              │  → HTML      │
              └──────────────┘
```

**Key Design Decisions:**
1. Tools are created dynamically from configured connectors
2. Claude decides which tools to call (not hardcoded)
3. Multi-step reasoning with tool results fed back
4. Structured JSON output parsed into `InvestigationReport`

---

## VS Code Extension Interaction Flow

```
Developer opens VS Code
        │
        ▼
┌─────────────────────────────┐
│ Extension activates         │
│ ├── Start API server        │
│ ├── Register commands        │
│ └── Create sidebar views     │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Developer clicks "Fetch     │
│ My Tasks" in sidebar        │
│                             │
│ POST /api/tasks             │
│ ← List of normalized tasks  │
│                             │
│ Tasks appear in tree view   │
│ with icons by type          │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Developer clicks a task     │
│                             │
│ POST /api/investigate       │
│ ← Progress notification     │
│                             │
│ AI investigation runs:      │
│ 1. Fetch task details       │
│ 2. Build context            │
│ 3. Claude reasoning         │
│ 4. Tool calls (if needed)   │
│ 5. Generate report          │
│                             │
│ ← InvestigationReport       │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Report opens in webview     │
│                             │
│ ┌─────────────────────────┐ │
│ │ Summary                 │ │
│ │ Root Cause Analysis     │ │
│ │ Findings (with conf.)   │ │
│ │ Recommendations         │ │
│ │ Affected Files          │ │
│ └─────────────────────────┘ │
│                             │
│ Investigation saved to      │
│ history for later review    │
└─────────────────────────────┘
```

---

## Future Architecture (Design Only)

These capabilities are architecturally supported but not yet implemented:

| Capability | Layer | Notes |
|-----------|-------|-------|
| AI Chatbot | Interface | WebSocket-based conversational UI |
| Streamlit Web UI | Interface | Browser-based investigation dashboard |
| Webhook Triggers | API | Auto-investigate on ticket creation |
| Data Lake Ingestion | Knowledge | Ingest from S3/Azure Blob for RAG |
| RAG Engine | Knowledge | Vector search over engineering docs |
| Enterprise SSO | Security | SAML/OIDC authentication |
| Multi-tenant | Storage | Shared server deployment |

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Claude API key | Yes |
| `TRACEAI_PORT` | API server port (default: 7420) | No |
| `TRACEAI_DATA_DIR` | Data directory (default: ~/.traceai) | No |
| `TRACEAI_LOG_LEVEL` | Log level (default: INFO) | No |

---

## License

MIT
