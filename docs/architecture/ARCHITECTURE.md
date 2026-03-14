# TraceAI — Architecture Reference

## Full System Architecture

```
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                    TASK ANALYZER — AI DEVELOPER ASSISTANT PLATFORM                                ║
║                              Modular AI-Powered Investigation Engine for Dev Teams                                ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────── INTERFACE LAYER ───────────────────────────────────────┐
│                                                                                              │
│  ┌──────────────────┐  ┌──────────────┐  ┌───────────────────┐  ┌─────────────────────────┐  │
│  │  VS Code Extension│  │     CLI      │  │ Streamlit Web UI  │  │    Chatbot / Copilot    │  │
│  │  ~~~~~~~~~~~~~~~~ │  │  ~~~~~~~~~~  │  │  ~~~~~~~~~~~~~~~  │  │  ~~~~~~~~~~~~~~~~~~~~   │  │
│  │ • Side Panel View │  │ • Interactive│  │ • Dashboard       │  │ • Slack / Teams Bot     │  │
│  │ • Inline Actions  │  │ • Batch Mode │  │ • Report Viewer   │  │ • Natural Language      │  │
│  │ • Tree View       │  │ • Pipe-able  │  │ • Config Editor   │  │ • Conversational Flow   │  │
│  │ • Status Bar Info │  │ • JSON Output│  │ • Graph Explorer  │  │ • Proactive Alerts      │  │
│  │    [ACTIVE]       │  │   [ACTIVE]   │  │     [FUTURE]      │  │       [FUTURE]          │  │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬──────────┘  └───────────┬─────────────┘  │
│           │                   │                    │                         │                │
└───────────┼───────────────────┼────────────────────┼─────────────────────────┼────────────────┘
            │                   │                    │                         │
            ▼                   ▼                    ▼                         ▼
┌──────────────────────────── API GATEWAY / PROTOCOL LAYER ────────────────────────────────────┐
│                                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                          UNIFIED REQUEST ROUTER & AUTH MIDDLEWARE                        │ │
│  │  • Request Validation  • Rate Limiting  • Session Management  • API Key Verification    │ │
│  └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                              │
│  ┌──────────────────┐  ┌────────────────────┐  ┌──────────────────┐  ┌───────────────────┐   │
│  │    REST API       │  │   WebSocket (WS)   │  │   Webhooks (In)  │  │  Event Bus (Int)  │   │
│  │  ~~~~~~~~~~~~~~   │  │  ~~~~~~~~~~~~~~~~  │  │  ~~~~~~~~~~~~~~  │  │  ~~~~~~~~~~~~~~~  │   │
│  │ POST /investigate │  │ Real-time stream   │  │ ADO webhook recv │  │ Internal pub/sub  │   │
│  │ GET  /results/:id │  │ Progress updates   │  │ Jira webhook recv│  │ Decoupled events  │   │
│  │ POST /analyze     │  │ Live reasoning     │  │ GitHub webhook   │  │ Retry & DLQ       │   │
│  │ GET  /projects    │  │ Token-by-token     │  │ Auto-trigger     │  │ Saga orchestrator │   │
│  │    [ACTIVE]       │  │    [ACTIVE]        │  │    [FUTURE]      │  │    [FUTURE]       │   │
│  └────────┬─────────┘  └────────┬───────────┘  └────────┬─────────┘  └────────┬──────────┘   │
│           │                     │                        │                     │              │
└───────────┼─────────────────────┼────────────────────────┼─────────────────────┼──────────────┘
            │                     │                        │                     │
            └──────────┬──────────┘                        └──────────┬──────────┘
                       │                                              │
                       ▼                                              ▼
┌───────────────────────────────────── CORE ENGINE LAYER ──────────────────────────────────────┐
│                                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                              LANGCHAIN ORCHESTRATOR                                      │ │
│  │                                                                                          │ │
│  │   ┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────────────┐      │ │
│  │   │ Chain Router │───▶│ Prompt Engine │───▶│ LLM Gateway  │───▶│ Response Parser    │      │ │
│  │   │             │    │              │    │              │    │                    │      │ │
│  │   │ • Plan step │    │ • Templates  │    │ • Anthropic  │    │ • JSON extraction  │      │ │
│  │   │ • Route     │    │ • Few-shot   │    │   Claude API │    │ • Confidence score │      │ │
│  │   │ • Retry     │    │ • CoT prompts│    │ • Future:    │    │ • Action items     │      │ │
│  │   │ • Fallback  │    │ • Guardrails │    │   OpenAI etc │    │ • Structured out   │      │ │
│  │   └─────────────┘    └──────────────┘    └──────────────┘    └────────────────────┘      │ │
│  └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                           │                                                  │
│              ┌────────────────────────────┼────────────────────────────┐                      │
│              ▼                            ▼                            ▼                      │
│  ┌─────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐         │
│  │ INVESTIGATION ENGINE│  │      TOOL ROUTER         │  │   REASONING PIPELINE     │         │
│  │ ~~~~~~~~~~~~~~~~~~~ │  │  ~~~~~~~~~~~~~~~~~~~~    │  │  ~~~~~~~~~~~~~~~~~~~~    │         │
│  │                     │  │                          │  │                          │         │
│  │ • Task Decomposer   │  │ • Dynamic Tool Registry  │  │ • Chain-of-Thought       │         │
│  │   - Break into sub  │  │ • Capability Matching    │  │ • Multi-hop Reasoning    │         │
│  │   - Dependency graph│  │ • Parallel Execution     │  │ • Evidence Accumulation  │         │
│  │                     │  │ • Result Aggregation     │  │ • Contradiction Detect   │         │
│  │ • Evidence Collector│  │                          │  │ • Hypothesis Ranking     │         │
│  │   - Multi-source    │  │ Available Tools:         │  │                          │         │
│  │   - Correlation     │  │ ┌──────┐ ┌──────┐       │  │ Reasoning Modes:         │         │
│  │   - Timeline build  │  │ │Search│ │Query │       │  │ ┌─────────────────────┐  │         │
│  │                     │  │ └──────┘ └──────┘       │  │ │ • Bug Root Cause    │  │         │
│  │ • Root Cause Finder │  │ ┌──────┐ ┌──────┐       │  │ │ • Story Breakdown   │  │         │
│  │   - Pattern match   │  │ │ File │ │ Code │       │  │ │ • Incident Triage   │  │         │
│  │   - Blame analysis  │  │ │ Read │ │Analyze│      │  │ │ • Code Review       │  │         │
│  │   - Impact scope    │  │ └──────┘ └──────┘       │  │ │ • Impact Analysis   │  │         │
│  │                     │  │ ┌──────┐ ┌──────┐       │  │ └─────────────────────┘  │         │
│  │ • Report Generator  │  │ │ Log  │ │Metric│       │  │                          │         │
│  │   - Markdown/HTML   │  │ │Parse │ │Fetch │       │  │ Output:                  │         │
│  │   - Confidence lvl  │  │ └──────┘ └──────┘       │  │  → Findings + Evidence   │         │
│  │   - Action items    │  │                          │  │  → Confidence Scores     │         │
│  └─────────┬───────────┘  └────────────┬─────────────┘  │  → Recommended Actions  │         │
│            │                           │                └────────────┬─────────────┘         │
└────────────┼───────────────────────────┼─────────────────────────────┼───────────────────────┘
             │                           │                             │
             └───────────┬───────────────┘                             │
                         │                                             │
                         ▼                                             │
┌──────────────────────────────────── KNOWLEDGE LAYER ─────────────────┼───────────────────────┐
│                                                                      │                       │
│  ┌──────────────────────────────────────────────────────┐            │                       │
│  │              PROJECT KNOWLEDGE PROFILE                │            │                       │
│  │                                                      │            │                       │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │            │                       │
│  │  │ Tech Stack   │  │  Conventions │  │ Architecture│ │            │                       │
│  │  │ • Languages  │  │  • Naming    │  │ • Modules  │ │            │                       │
│  │  │ • Frameworks │  │  • Patterns  │  │ • Services │ │            │                       │
│  │  │ • Databases  │  │  • Branching │  │ • Data flow│ │            │                       │
│  │  └──────────────┘  └──────────────┘  └────────────┘ │            │                       │
│  └──────────────────────────┬───────────────────────────┘            │                       │
│                             │                                        │                       │
│         ┌───────────────────┼──────────────────────┐                 │                       │
│         ▼                   ▼                      ▼                 ▼                       │
│  ┌──────────────┐  ┌───────────────────┐  ┌───────────────────────────────┐                  │
│  │  RAG ENGINE   │  │  CONTEXT BUILDER  │  │     INVESTIGATION MEMORY      │                  │
│  │  [FUTURE]     │  │                   │  │                               │                  │
│  │ • Embeddings │  │ • Scope Resolver  │  │ • Past investigation recall   │                  │
│  │ • Vector DB  │  │ • Relevance Rank  │  │ • Similar issue matching      │                  │
│  │ • Semantic   │  │ • Token Budget    │  │ • Pattern recognition         │                  │
│  │   Search     │  │ • Multi-source    │  │ • Learning from resolutions   │                  │
│  └──────────────┘  └────────┬──────────┘  └───────────────┬───────────────┘                  │
│                             │                             │                                  │
└─────────────────────────────┼─────────────────────────────┼──────────────────────────────────┘
                              │                             │
                              ▼                             │
┌──────────────────────────────── CONNECTOR LAYER (PLUGINS) ┼──────────────────────────────────┐
│                                                           │                                  │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                          PLUGIN SYSTEM — DYNAMIC CONNECTOR REGISTRY                      │ │
│  │                                                                                          │ │
│  │  BaseConnector Interface:                                                                │ │
│  │  ┌───────────────────────────────────────────────────────────────────────────────────┐    │ │
│  │  │ + validate_connection()  + fetch_tasks()  + get_task_detail()  + search()         │    │ │
│  │  │ + get_context()          + disconnect()   + get_setup_questions()                 │    │ │
│  │  └───────────────────────────────────────────────────────────────────────────────────┘    │ │
│  └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                              │
│  ┌────── Work Tracking ──────┐  ┌──── Source & Docs ────┐  ┌──── Data & Monitoring ────┐    │
│  │                           │  │                        │  │                           │    │
│  │  ┌─────────────────────┐  │  │  ┌──────────────────┐  │  │  ┌─────────────────────┐  │    │
│  │  │    Azure DevOps     │  │  │  │     GitHub       │  │  │  │   SQL Databases     │  │    │
│  │  │  • Work Items (WIT) │  │  │  │  • Issues/PRs   │  │  │  │  • Read-only Query  │  │    │
│  │  │  • Boards / Sprints │  │  │  │  • Commits/Diff │  │  │  │  • Schema Inspect   │  │    │
│  │  │  • Repos / Commits  │  │  │  │  • Code Search  │  │  │  │  • Data Sampling    │  │    │
│  │  └─────────────────────┘  │  │  └──────────────────┘  │  │  └─────────────────────┘  │    │
│  │                           │  │                        │  │                           │    │
│  │  ┌─────────────────────┐  │  │  ┌──────────────────┐  │  │  ┌─────────────────────┐  │    │
│  │  │       Jira          │  │  │  │   Confluence     │  │  │  │    Grafana          │  │    │
│  │  │  • Issues / Epics   │  │  │  │  • Page Search  │  │  │  │  • Dashboard Query  │  │    │
│  │  │  • Sprint Boards    │  │  │  │  • Space Browse │  │  │  │  • Metric Fetch     │  │    │
│  │  │  • Comments / Links │  │  │  │  • Runbook Fetch│  │  │  │  • Alert History    │  │    │
│  │  └─────────────────────┘  │  │  └──────────────────┘  │  │  └─────────────────────┘  │    │
│  │                           │  │                        │  │                           │    │
│  └───────────────────────────┘  └────────────────────────┘  └───────────────────────────┘    │
│                                                                                              │
│  ┌────── CRM / Support ──────┐  ┌──── AI / Protocol ────┐  ┌──── Extensibility ────────┐    │
│  │                           │  │                        │  │                           │    │
│  │  ┌─────────────────────┐  │  │  ┌──────────────────┐  │  │  ┌─────────────────────┐  │    │
│  │  │    Salesforce       │  │  │  │  MCP (Model      │  │  │  │  Custom Connectors  │  │    │
│  │  │  • Case Lookup      │  │  │  │  Context Proto.) │  │  │  │  • Plugin Template  │  │    │
│  │  │  • Account Context  │  │  │  │  • Tool Serving  │  │  │  │  • Auto-Discovery   │  │    │
│  │  │  • Knowledge Base   │  │  │  │  • Auto-detect   │  │  │  │  • Hot Reload       │  │    │
│  │  └─────────────────────┘  │  │  │  • Bi-directional│  │  │  │    [FUTURE]         │  │    │
│  │                           │  │  └──────────────────┘  │  │  └─────────────────────┘  │    │
│  └───────────────────────────┘  └────────────────────────┘  └───────────────────────────┘    │
│                                                                                              │
└──────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                               │
           ┌───────────────────────────────────┼───────────────────────────────────┐
           ▼                                   ▼                                   ▼
┌──────────────────────────────────── SECURITY LAYER ──────────────────────────────────────────┐
│                                                                                              │
│  ┌──────────────────────┐  ┌─────────────────────────┐  ┌────────────────────────────────┐   │
│  │   OS KEYCHAIN        │  │  CREDENTIAL MANAGER     │  │    SECURITY POLICIES           │   │
│  │                      │  │                         │  │                                │   │
│  │ • Windows Cred Store │  │ • Store / Retrieve      │  │ • No secrets in logs/cache     │   │
│  │ • macOS Keychain     │  │ • Delete / Exists       │  │ • No plaintext on disk ever    │   │
│  │ • Linux Secret Svc   │  │ • Namespaced per        │  │ • Least-privilege access       │   │
│  │ • No plaintext ever  │  │   connector instance    │  │ • User-informed transparency   │   │
│  └──────────────────────┘  └─────────────────────────┘  └────────────────────────────────┘   │
│                                                                                              │
└──────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                               │
                                               ▼
┌───────────────────────────────────── STORAGE LAYER ──────────────────────────────────────────┐
│                                                                                              │
│  ┌────────────────────────┐  ┌─────────────────────────┐  ┌──────────────────────────────┐   │
│  │   ~/.task-analyzer/    │  │    CONFIG STORE          │  │   INVESTIGATION CACHE        │   │
│  │                        │  │                         │  │                              │   │
│  │ ├── config.json        │  │ • Platform settings     │  │ • TTL-based expiry           │   │
│  │ ├── profiles/          │  │ • Connector configs     │  │ • API response caching       │   │
│  │ │   └── <repo>.json    │  │ • No secrets (ever)     │  │ • Parsed work item cache     │   │
│  │ ├── investigations/    │  │ • JSON, schema-valid    │  │                              │   │
│  │ │   └── <id>.json      │  │                         │  │ Future:                      │   │
│  │ └── cache/             │  │                         │  │ • SQLite for structured data │   │
│  │                        │  │                         │  │ • Data lake ingestion        │   │
│  └────────────────────────┘  └─────────────────────────┘  └──────────────────────────────┘   │
│                                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════ DATA FLOW ═════════════════════════════════════════════

  User Request                                            Investigation Result
       │                                                         ▲
       ▼                                                         │
  ┌─────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐  │
  │Interface│───▶│API Gate- │───▶│  Core     │───▶│ Report   │──┘
  │ Layer   │    │way Layer │    │  Engine   │    │Generator │
  └─────────┘    └──────────┘    └─────┬─────┘    └──────────┘
                                       │
                      ┌────────────────┼────────────────┐
                      ▼                ▼                ▼
                ┌──────────┐    ┌──────────┐    ┌──────────┐
                │Knowledge │    │Connector │    │ Storage  │
                │  Layer   │◀──▶│  Layer   │    │  Layer   │
                └──────────┘    └─────┬────┘    └─────▲────┘
                                      │               │
                                      ▼               │
                                ┌──────────┐          │
                                │ Security │──────────┘
                                │  Layer   │ (encrypted read/write)
                                └──────────┘

  TYPICAL INVESTIGATION FLOW:
  ═══════════════════════════
  1. User triggers investigation via VS Code / CLI with task ID
  2. API Gateway validates request, creates session
  3. Core Engine builds investigation plan
  4. Knowledge Layer provides project context & past patterns
  5. Tool Router dispatches queries to Connectors:
     ├─▶ Azure DevOps / Jira / GitHub: fetch work item + linked items
     ├─▶ Confluence: search related documentation
     ├─▶ SQL: query related data + schema analysis
     └─▶ Grafana: fetch error metrics + recent alerts
  6. LangChain orchestrates multi-step Claude reasoning
  7. Claude decides which tools to call dynamically
  8. Report Generator produces structured findings + confidence scores
  9. Results returned via REST API → rendered in VS Code webview
```

## Layer Responsibilities

| Layer | Responsibility | Status |
|-------|---------------|--------|
| Interface | User interaction (VS Code, CLI) | **Active** |
| API Gateway | REST endpoints, request routing | **Active** |
| Core Engine | LangChain + Claude orchestration | **Active** |
| Knowledge | Repository scanning, project profiles | **Active** |
| Connector | Plugin system, 8 connectors | **Active** |
| Security | OS keychain credential management | **Active** |
| Storage | JSON file persistence, caching | **Active** |

## Future Capabilities (Architecture Ready)

| Capability | Layer | Status |
|-----------|-------|--------|
| Streamlit Web UI | Interface | Design only |
| AI Chatbot | Interface | Design only |
| Webhook auto-triggers | API Gateway | Design only |
| RAG over engineering docs | Knowledge | Design only |
| Data lake ingestion | Storage | Design only |
| Enterprise SSO | Security | Design only |
| Custom connector SDK | Connector | Design only |
