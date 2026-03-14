# TraceAI — Team Installation Guide

## Prerequisites

- **Python 3.11+** installed and available in PATH
- **VS Code** 1.85 or later
- **Anthropic API key** (for Claude-powered investigations)

## Installation

1. Download the file:

   ```
   traceai-0.2.0.vsix
   ```

2. Open **VS Code**

3. Go to the **Extensions** panel (Ctrl+Shift+X)

4. Click the **three-dot menu** (⋯) at the top of the Extensions panel

5. Select **Install from VSIX...**

6. Choose the `traceai-0.2.0.vsix` file

7. **Restart VS Code** when prompted

## What Happens Automatically

When VS Code starts after installation, TraceAI will:

1. Detect your Python installation
2. Create a virtual environment at `~/.traceai/runtime/venv`
3. Install the TraceAI backend automatically
4. Start the investigation server on `localhost:7420`
5. Show the TraceAI sidebar in the activity bar

**You do not need to clone any repository or run any setup scripts.**

## First-Time Setup

On first launch, TraceAI will prompt:

> "Welcome to TraceAI. Run setup to configure your ticket source."

Click **Run Setup** to configure:

- Your **ticket source** (Azure DevOps, Jira, or GitHub Issues)
- Your **Anthropic API key** (stored securely in OS keychain)
- Optional connectors (SQL Database, Grafana, Confluence)

## Setting Your API Key

If you haven't set your Anthropic API key as an environment variable:

**Windows:**
```
setx ANTHROPIC_API_KEY "sk-ant-..."
```

**macOS / Linux:**
```
export ANTHROPIC_API_KEY="sk-ant-..."
```

Add the export to your shell profile (`~/.bashrc`, `~/.zshrc`) for persistence.

## Daily Usage

1. Open VS Code — TraceAI starts automatically
2. Your assigned tasks appear in the **TraceAI sidebar**
3. **Click any task** to start an AI investigation
4. View the structured report with findings and recommendations
5. Tasks refresh automatically every 5 minutes

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Python 3.11+ required" | Install Python from https://python.org |
| Server won't start | Open command palette → "TraceAI: Show Status" |
| No tasks showing | Run "TraceAI: Run Setup Wizard" to configure ticket source |
| Investigation fails | Check that ANTHROPIC_API_KEY is set |

### View Server Logs

If the server fails to start, check the output channel:

1. Open **View → Output** (Ctrl+Shift+U)
2. Select **TraceAI Server** from the dropdown
3. Review the log messages

## Data Location

All TraceAI data is stored locally:

```
~/.traceai/
├── config.json          # Configuration (no secrets)
├── runtime/venv/        # Python virtual environment
├── cache/tasks.json     # Cached task list
├── investigations/      # Investigation reports
└── logs/                # Audit logs
```

## Uninstalling

1. Open Extensions panel in VS Code
2. Find TraceAI → click **Uninstall**
3. Optionally delete `~/.traceai/` to remove all data
