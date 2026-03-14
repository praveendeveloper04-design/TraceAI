"""
API Server — FastAPI backend for the VS Code extension and future UIs.

Provides REST endpoints for:
  - Task listing and detail
  - Investigation triggering and status
  - Configuration management
  - Project profile access
  - Health check for server liveness

The VS Code extension communicates with this server over HTTP/WebSocket.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from task_analyzer import __version__

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="TraceAI API",
    description="AI-Powered Developer Investigation Platform",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # VS Code extension
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Startup Diagnostics ─────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup_diagnostics():
    """Log key configuration state at server startup."""
    from task_analyzer.investigation.engine import _sync_anthropic_env_vars

    logger.info("server_starting", version=__version__, port=7420)

    # Sync all Anthropic env vars from Windows registry at startup
    _sync_anthropic_env_vars()

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")

    if key:
        logger.info(
            "startup_llm_key_loaded",
            key_length=len(key),
            key_prefix=key[:8] + "..." if len(key) > 8 else "****",
            base_url=base_url or "https://api.anthropic.com (default)",
        )
    else:
        logger.error(
            "startup_llm_key_missing",
            message="Investigations will fail. Set ANTHROPIC_API_KEY or add to credentials.json.",
        )


# ── Request/Response Models ───────────────────────────────────────────────────

class InvestigateRequest(BaseModel):
    task_id: str


class TaskListRequest(BaseModel):
    assigned_to: str | None = None
    query: str | None = None
    max_results: int = 50
    statuses: list[str] | None = None       # Filter by task statuses
    workspace_path: str | None = None       # Current workspace path


class StatusResponse(BaseModel):
    version: str
    configured: bool
    ticket_source: str | None
    repositories: int
    connectors: int
    profiles: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for server liveness detection."""
    return {"status": "ok"}


@app.get("/api/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get the current platform status."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()

    return StatusResponse(
        version=__version__,
        configured=config is not None,
        ticket_source=config.ticket_source.connector_type.value if config and config.ticket_source else None,
        repositories=len(config.repositories) if config else 0,
        connectors=len(config.connectors) if config else 0,
        profiles=len(store.list_profiles()),
    )


@app.post("/api/tasks")
async def list_tasks(request: TaskListRequest) -> list[dict[str, Any]]:
    """Fetch tasks from the configured ticket source."""
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        raise HTTPException(status_code=400, detail="No ticket source configured")

    logger.info(
        "list_tasks_request",
        ticket_source=config.ticket_source.connector_type.value,
        connector_name=config.ticket_source.name,
        assigned_to=request.assigned_to,
        statuses=request.statuses,
        settings_keys=list(config.ticket_source.settings.keys()),
        credential_keys=config.ticket_source.credential_keys,
    )

    registry = create_default_registry()
    connector = registry.create(config.ticket_source)

    try:
        await connector.validate_connection()
        tasks = await connector.fetch_tasks(
            assigned_to=request.assigned_to,
            query=request.query,
            max_results=request.max_results,
        )

        # Filter by statuses if provided
        if request.statuses:
            tasks = [t for t in tasks if t.status.value in request.statuses]

        logger.info("list_tasks_success", count=len(tasks))
        return [t.model_dump() for t in tasks]
    except Exception as exc:
        logger.error("list_tasks_failed", error=str(exc), error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await connector.disconnect()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    """Get detailed information about a specific task."""
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        raise HTTPException(status_code=400, detail="No ticket source configured")

    registry = create_default_registry()
    connector = registry.create(config.ticket_source)

    try:
        await connector.validate_connection()
        task = await connector.get_task_detail(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return task.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await connector.disconnect()


@app.post("/api/investigate")
async def investigate(request: InvestigateRequest) -> dict[str, Any]:
    """Start an AI investigation on a task."""
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.investigation.engine import InvestigationEngine
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        raise HTTPException(status_code=400, detail="No ticket source configured")

    registry = create_default_registry()
    ticket_connector = registry.create(config.ticket_source)

    # Initialize optional connectors — failures are logged, not fatal
    for conn_config in config.connectors:
        if conn_config.enabled:
            try:
                registry.create(conn_config)
            except Exception as exc:
                logger.warning(
                    "optional_connector_init_failed",
                    connector=conn_config.name,
                    connector_type=conn_config.connector_type.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    try:
        await ticket_connector.validate_connection()
        task = await ticket_connector.get_task_detail(request.task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{request.task_id}' not found")

        # Load profiles
        profiles = []
        for repo_path in config.repositories:
            profile = store.load_profile(Path(repo_path).name)
            if profile:
                profiles.append(profile)

        # Run investigation — the engine handles all internal failures gracefully
        engine = InvestigationEngine(config=config, registry=registry, profiles=profiles)
        report = await engine.investigate(task)
        store.save_investigation(report)

        logger.info(
            "investigation_api_complete",
            task_id=request.task_id,
            status=report.status.value,
            findings=len(report.findings),
            has_warnings=bool(report.error),
        )

        return report.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "investigation_api_failed",
            task_id=request.task_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    finally:
        await registry.disconnect_all()


@app.get("/api/investigations")
async def list_investigations(limit: int = 20) -> list[dict[str, Any]]:
    """List recent investigations."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    return store.list_investigations()[:limit]


@app.get("/api/investigations/{report_id}")
async def get_investigation(report_id: str) -> dict[str, Any]:
    """Get a specific investigation report."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    report = store.load_investigation(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Investigation '{report_id}' not found")
    return report.model_dump()


@app.get("/api/investigations/{report_id}/markdown")
async def get_investigation_markdown(report_id: str) -> dict[str, str]:
    """Get an investigation report as Markdown."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    report = store.load_investigation(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Investigation '{report_id}' not found")
    return {"markdown": report.to_markdown()}


@app.get("/api/profiles")
async def list_profiles() -> list[dict[str, Any]]:
    """List all project profiles."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    profiles = []
    for name in store.list_profiles():
        p = store.load_profile(name)
        if p:
            profiles.append({
                "id": p.id,
                "repo_name": p.repo_name,
                "primary_language": p.primary_language,
                "services_count": len(p.services),
                "scanned_at": str(p.scanned_at),
            })
    return profiles


def start_server(host: str = "127.0.0.1", port: int = 7420) -> None:
    """Start the API server."""
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()
