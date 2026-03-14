# TraceAI Security Model

TraceAI is designed as a **read-only investigation assistant**.
It analyzes your code, tickets, and systems — it never changes them.

## Security Guarantees

### Repository Safety
- TraceAI **never** performs `git commit`, `git push`, or `git reset`
- TraceAI **never** modifies, creates, or deletes files in your repository
- Repository access is strictly read-only: file reading, git log, git diff, git blame
- All repository operations are validated through the SecurityGuard before execution
- Blocked git operations: commit, push, reset, clean, checkout, rebase, merge, stash, rm, mv

### Database Safety
- All database queries are **read-only** (SELECT only)
- INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, EXEC, EXECUTE, GRANT, REVOKE, MERGE, CALL, REPLACE, LOAD, and RENAME are all blocked
- Compound SQL statements (semicolon-separated) are rejected
- SQL comments are stripped before validation to prevent bypass attacks
- Query execution has a 30-second timeout
- Every query is double-validated: once by the SecurityGuard and once by the SQL connector

### Ticket System Safety
- TraceAI only **reads** tickets — it never creates, updates, or deletes them
- All ticket system access is read-only: fetch, search, get details
- Supported systems: Azure DevOps, Jira, GitHub Issues

### Credential Safety
- All credentials are stored in your **OS keychain** (never on disk)
  - macOS: Keychain
  - Windows: Credential Manager
  - Linux: Secret Service (GNOME Keyring / KWallet)
- No credentials are ever written to configuration files
- No credentials are included in investigation reports or logs
- Credentials are namespaced under `traceai/` to avoid collisions

### Safe Mode
- Safe Mode is **enabled by default** for all users
- Safe Mode enforces read-only access across all connectors
- Every tool must be registered in the Tool Permission Registry
- Unregistered tools are blocked from execution
- Tool permissions are declared statically — they cannot be modified at runtime
- The Tool Permission Registry includes: RepoReader, TicketReader, LogReader, DBReader

### Privacy
- TraceAI includes **zero telemetry** — no data is sent anywhere
- No analytics, no tracking, no remote reporting
- All data stays on your machine in `~/.traceai/`
- Investigation reports are stored locally only
- No remote code execution — no subprocess, exec, or eval in the investigation engine

### Rate Limiting
- All connector operations are rate-limited to prevent API abuse:
  - Ticket systems (Azure DevOps, Jira, GitHub): 30-second minimum interval
  - SQL Database: 5-second minimum interval
  - Grafana/MCP: 10-second minimum interval
- Timeout protection prevents investigation hangs:
  - Repository operations: 10 seconds
  - Ticket queries: 20 seconds
  - Database queries: 30 seconds
  - Log queries: 15 seconds

### Audit Trail
- All investigation operations are logged to `~/.traceai/logs/investigation.log`
- Logs include: timestamp, ticket ID, tools used, operation status, duration
- Security violations are logged separately for review
- Logs are structured JSON (JSONL format) for easy parsing

### Server Safety
- The Python backend runs on localhost only (127.0.0.1:7420)
- No external network listeners
- Crash recovery: up to 3 automatic restart attempts
- CORS is configured for VS Code extension access only

## Architecture Security Layers

```
VS Code Extension
    │
    ▼
Python Backend (localhost:7420)
    │
    ▼
┌─────────────────────────────────┐
│     SECURITY GUARD LAYER        │
│                                 │
│  Tool Permission Registry       │
│  ├─ RepoReader   (read, safe)  │
│  ├─ TicketReader (read, safe)  │
│  ├─ LogReader    (read, safe)  │
│  └─ DBReader     (read, safe)  │
│                                 │
│  Validates EVERY operation      │
│  Enforces Safe Mode             │
│  Logs all operations            │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│     RATE LIMITER LAYER          │
│                                 │
│  Min intervals per connector    │
│  Timeout protection             │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│     CONNECTOR LAYER             │
│                                 │
│  Read-only access to:           │
│  ├─ Azure DevOps / Jira / GH   │
│  ├─ SQL Databases (SELECT only) │
│  ├─ Grafana (dashboards/logs)   │
│  └─ Confluence (wiki search)    │
└─────────────────────────────────┘
    │
    ▼
Audit Log (~/.traceai/logs/)
```

## Reporting Security Issues

If you discover a security vulnerability in TraceAI, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Email the maintainers directly with details of the vulnerability
3. Include steps to reproduce the issue
4. Allow reasonable time for a fix before public disclosure

## Verification

You can verify TraceAI's security guarantees by:

1. Checking `~/.traceai/logs/investigation.log` after any investigation
2. Reviewing the SecurityGuard source code in `src/task_analyzer/core/security_guard.py`
3. Running the security test suite (when available)
4. Inspecting the Tool Permission Registry in the same file
