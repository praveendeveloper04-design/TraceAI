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

import asyncio
import json as _json
import os
from datetime import datetime, timezone
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
            key_prefix=key[:4] + "..." if len(key) > 4 else "****",
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

    # Initialize optional connectors -- validate SQL for schema discovery
    for conn_config in config.connectors:
        if conn_config.enabled:
            try:
                conn = registry.create(conn_config)
                # Validate SQL connector so planner can use it for schema discovery
                if conn_config.connector_type.value == "sql_database":
                    try:
                        await conn.validate_connection()
                        logger.info("sql_connector_validated_for_planner")
                    except Exception as sql_exc:
                        logger.warning("sql_connector_validation_failed", error=str(sql_exc)[:100])
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

        # Run investigation with state tracking
        from task_analyzer.core.investigation_state import InvestigationState, investigation_registry
        import uuid

        engine = InvestigationEngine(config=config, registry=registry, profiles=profiles)

        # Pre-create the report ID so we can track it
        investigation_id = str(uuid.uuid4())

        inv_state = InvestigationState(investigation_id, request.task_id, task.title)
        investigation_registry.register(inv_state)

        async def _progress(stage: str, message: str) -> None:
            progress_map = {
                "loading_ticket": 3,
                "classifying": 10, "parallel_analysis": 15,
                "parallel_execution": 20, "deep_investigation": 30,
                "skills_execution": 35, "sql_intelligence": 45,
                "evidence_aggregation": 55, "building_graph": 60,
                "building_context": 70, "ai_reasoning": 85,
                "generating_report": 95,
            }
            inv_state.set_step(stage, progress_map.get(stage, inv_state.progress))

        try:
            report = await engine.investigate(task, progress_callback=_progress)
        except asyncio.CancelledError:
            inv_state.complete("cancelled")
            return {"id": investigation_id, "status": "cancelled", "task_id": request.task_id}

        # Override the report ID to match our tracked ID
        report.id = investigation_id
        store.save_investigation(report)
        inv_state.complete(report.status.value)

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


@app.get("/api/investigate/{task_id}/stream")
async def investigate_stream(task_id: str):
    """
    Stream investigation progress via Server-Sent Events (SSE).

    Emits progress events as the investigation runs, then a final
    'complete' event with the full report.
    """
    from fastapi.responses import StreamingResponse
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.investigation.engine import InvestigationEngine
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        async def _error():
            yield f"event: error\ndata: {_json.dumps({'error': 'No ticket source configured'})}\n\n"
        return StreamingResponse(_error(), media_type="text/event-stream")

    async def _stream():
        progress_queue: asyncio.Queue = asyncio.Queue()

        # Progress percentage map for each stage
        PROGRESS_MAP = {
            "loading_ticket": 5,
            "classifying": 8,
            "initializing_workspace": 10,
            "parallel_analysis": 15,
            "parallel_execution": 20,
            "deep_investigation": 35,
            "sql_intelligence": 50,
            "sql_queries": 50,
            "evidence_aggregation": 60,
            "building_graph": 65,
            "graph_build": 65,
            "building_context": 70,
            "ai_reasoning": 80,
            "generating_report": 90,
            "report_generation": 90,
        }

        async def _on_progress(stage: str, message: str):
            event = {
                "stage": stage,
                "message": message,
                "progress": PROGRESS_MAP.get(stage, 50),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await progress_queue.put(("progress", event))

        # Run investigation in a background task
        async def _run():
            registry = create_default_registry()
            ticket_connector = registry.create(config.ticket_source)
            for conn_config in config.connectors:
                if conn_config.enabled:
                    try:
                        registry.create(conn_config)
                    except Exception:
                        pass
            try:
                await ticket_connector.validate_connection()
                task = await ticket_connector.get_task_detail(task_id)
                if not task:
                    await progress_queue.put(("error", {"error": f"Task '{task_id}' not found"}))
                    return

                profiles = []
                for repo_path in config.repositories:
                    profile = store.load_profile(Path(repo_path).name)
                    if profile:
                        profiles.append(profile)

                engine = InvestigationEngine(config=config, registry=registry, profiles=profiles)
                report = await engine.investigate(task, progress_callback=_on_progress)
                store.save_investigation(report)
                await progress_queue.put(("complete", report.model_dump()))
            except Exception as exc:
                await progress_queue.put(("error", {"error": str(exc)}))
            finally:
                await registry.disconnect_all()
                await progress_queue.put(("done", None))

        # Start investigation in background
        asyncio.create_task(_run())

        # Yield SSE events from the queue
        while True:
            event_type, data = await progress_queue.get()
            if event_type == "done":
                break
            yield f"event: {event_type}\ndata: {_json.dumps(data, default=str)}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


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


@app.delete("/api/investigations")
async def delete_all_investigations() -> dict[str, Any]:
    """Delete all investigation history."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    files = list(store.investigations_dir.glob("*.json"))
    count = 0
    for f in files:
        try:
            f.unlink()
            count += 1
        except Exception:
            pass
    logger.info("investigations_deleted_all", count=count)
    return {"success": True, "deleted": count}


@app.delete("/api/investigations/{report_id}")
async def delete_investigation(report_id: str) -> dict[str, Any]:
    """Delete a single investigation report."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    path = store.investigations_dir / f"{report_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Investigation '{report_id}' not found")
    path.unlink()
    logger.info("investigation_deleted", id=report_id)
    return {"success": True}


@app.get("/api/investigation/{investigation_id}/status")
async def get_investigation_status(investigation_id: str) -> dict[str, Any]:
    """Get live status of a running investigation."""
    from task_analyzer.core.investigation_state import investigation_registry

    state = investigation_registry.get(investigation_id)
    if state:
        return state.to_dict()
    # Fall back to stored report
    from task_analyzer.storage.local_store import LocalStore
    store = LocalStore()
    report = store.load_investigation(investigation_id)
    if report:
        return {
            "id": report.id,
            "task_id": report.task_id,
            "task_title": report.task_title,
            "status": report.status.value,
            "step": "done",
            "progress": 100,
            "logs": [],
            "started_at": str(report.started_at),
            "finished_at": str(report.completed_at),
        }
    raise HTTPException(status_code=404, detail="Investigation not found")


@app.post("/api/investigation/{investigation_id}/cancel")
async def cancel_investigation(investigation_id: str) -> dict[str, Any]:
    """Cancel a running investigation."""
    from task_analyzer.core.investigation_state import investigation_registry

    if investigation_registry.cancel(investigation_id):
        return {"success": True, "status": "cancelled"}
    raise HTTPException(status_code=404, detail="Investigation not found or already completed")


@app.get("/api/validate")
async def validate_system() -> list[dict]:
    """Run all system validation checks."""
    from task_analyzer.core.validation import validate_all

    results = await validate_all()
    return [r.to_dict() for r in results]


class GeneratePatchRequest(BaseModel):
    investigation_id: str
    workspace_path: str | None = None


@app.post("/api/generate-patch")
async def generate_patch(request: GeneratePatchRequest) -> dict[str, Any]:
    """
    Generate a code patch from an investigation report using Claude.

    Returns a patch object with file diffs that can be previewed
    and applied by the VS Code extension.
    """
    from task_analyzer.storage.local_store import LocalStore
    from task_analyzer.investigation.engine import _create_llm, _sync_anthropic_env_vars
    from task_analyzer.models.schemas import PlatformConfig
    from langchain_core.messages import HumanMessage, SystemMessage

    store = LocalStore()
    report = store.load_investigation(request.investigation_id)
    if not report:
        raise HTTPException(status_code=404, detail="Investigation not found")

    config = store.load_config() or PlatformConfig()

    # Build the patch generation prompt from the investigation report
    prompt_parts = [
        f"# Investigation Report: {report.task_title}",
        f"\n## Summary\n{report.summary}",
    ]
    if report.root_cause:
        prompt_parts.append(f"\n## Root Cause\n{report.root_cause}")
    if report.recommendations:
        prompt_parts.append("\n## Recommendations")
        for r in report.recommendations:
            prompt_parts.append(f"- {r}")
    if report.affected_files:
        prompt_parts.append("\n## Affected Files")
        for f in report.affected_files:
            prompt_parts.append(f"- {f}")

    context = "\n".join(prompt_parts)

    try:
        _sync_anthropic_env_vars()
        llm = _create_llm(config)

        messages = [
            SystemMessage(content=(
                "You are a senior software engineer. Based on the investigation report below, "
                "generate a minimal code patch to fix the identified issue.\n\n"
                "Output format: a JSON object with this structure:\n"
                '{"files": [{"path": "relative/path/to/file.py", '
                '"description": "What this change does", '
                '"original": "exact lines to replace", '
                '"patched": "replacement lines"}]}\n\n'
                "Rules:\n"
                "- Only modify the minimum lines necessary\n"
                "- Include enough context in 'original' to uniquely identify the location\n"
                "- Do not modify unrelated code\n"
                "- If you cannot determine the exact fix, return an empty files array\n"
            )),
            HumanMessage(content=f"Generate a patch for this investigation:\n\n{context}"),
        ]

        response = await llm.ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # Parse the patch JSON
        import json as _json
        patch_data = {"files": [], "raw_response": content}
        try:
            if "```json" in content:
                start = content.index("```json") + 7
                end = content.index("```", start)
                patch_data = _json.loads(content[start:end].strip())
            elif "```" in content:
                start = content.index("```") + 3
                end = content.index("```", start)
                patch_data = _json.loads(content[start:end].strip())
            else:
                patch_data = _json.loads(content)
        except (_json.JSONDecodeError, ValueError):
            patch_data["parse_error"] = "Could not parse patch JSON from LLM response"

        patch_data["investigation_id"] = request.investigation_id
        patch_data["task_title"] = report.task_title

        logger.info(
            "patch_generated",
            investigation_id=request.investigation_id,
            file_count=len(patch_data.get("files", [])),
        )

        return patch_data

    except Exception as exc:
        logger.error("patch_generation_failed", error=str(exc), error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Patch generation failed: {exc}")

# ── Workspace Index Endpoints ────────────────────────────────────────────────

@app.get("/api/index/status")
async def get_index_status() -> dict[str, Any]:
    """
    Returns current workspace index state.

    Responses:
      - {"status": "fresh", "classes": N, "repos": [...]} — no indexing needed
      - {"status": "stale", "stale_repos": [...]} — needs indexing
      - {"status": "unconfigured"} — no workspace profile exists
    """
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config:
        return {"status": "unconfigured"}

    # Load workspace profile
    try:
        from task_analyzer.knowledge.workspace_scanner import load_workspace_profile
        workspace = load_workspace_profile()
        if not workspace.repos:
            return {"status": "unconfigured", "reason": "no_repos"}
    except Exception as exc:
        logger.debug("index_status_no_workspace", error=str(exc))
        return {"status": "unconfigured", "reason": str(exc)}

    # Check workspace index freshness
    try:
        from task_analyzer.workspace_intelligence.index import WorkspaceIndex
        index = WorkspaceIndex()

        stale_repos = []
        all_repos = []
        for repo_info in workspace.repos:
            repo_name = repo_info.get("name", "")
            repo_path = repo_info.get("path", "")
            if not repo_name or not repo_path:
                continue
            all_repos.append({"name": repo_name, "path": repo_path})
            age = index.get_repo_scan_age(repo_name)
            if age is None or age > 86400:  # 24 hours
                stale_repos.append({"name": repo_name, "path": repo_path})

        if stale_repos:
            return {
                "status": "stale",
                "stale_repos": [r["name"] for r in stale_repos],
                "all_repos": [r["name"] for r in all_repos],
            }

        stats = index.get_stats()
        return {
            "status": "fresh",
            "classes": stats.get("classes", 0),
            "methods": stats.get("methods", 0),
            "api_routes": stats.get("api_routes", 0),
            "repos": [r["name"] for r in all_repos],
        }
    except Exception as exc:
        logger.debug("index_status_check_failed", error=str(exc))
        return {"status": "stale", "stale_repos": [r.get("name", "") for r in workspace.repos], "error": str(exc)}


@app.post("/api/index")
async def run_index() -> dict[str, Any]:
    """
    Run workspace indexing synchronously for all stale repos.

    Indexes all repositories that haven't been indexed or are stale (>24h).
    Builds schema relations if needed.

    Returns:
      {"status": "complete", "classes": N, "duration_ms": M, "repos_indexed": [...]}
    """
    import time as _time
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config:
        raise HTTPException(status_code=400, detail="Not configured")

    # Load workspace profile
    try:
        from task_analyzer.knowledge.workspace_scanner import load_workspace_profile
        workspace = load_workspace_profile()
        if not workspace.repos:
            return {"status": "complete", "classes": 0, "duration_ms": 0, "repos_indexed": [], "message": "No repos configured"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No workspace profile: {exc}")

    start = _time.time()
    repos_indexed = []

    try:
        from task_analyzer.workspace_intelligence.index import WorkspaceIndex
        index = WorkspaceIndex()

        # Index stale repos
        for repo_info in workspace.repos:
            repo_name = repo_info.get("name", "")
            repo_path = repo_info.get("path", "")
            if not repo_name or not repo_path:
                continue
            age = index.get_repo_scan_age(repo_name)
            if age is None or age > 86400:  # 24 hours
                logger.info("indexing_repository_api", repo=repo_name)
                index.index_repository(repo_name, repo_path)
                repos_indexed.append(repo_name)
                logger.info("repository_indexed_api", repo=repo_name)

        # Build schema relations if needed
        try:
            fk_count = index._get_conn().execute(
                "SELECT COUNT(*) FROM db_foreign_keys"
            ).fetchone()[0]
            if fk_count == 0 and config.connectors:
                # Find SQL connector for schema discovery
                from task_analyzer.connectors import create_default_registry
                registry = create_default_registry()
                db_conn = None
                for conn_config in config.connectors:
                    if conn_config.enabled and conn_config.connector_type.value == "sql_database":
                        try:
                            db_conn = registry.create(conn_config)
                            break
                        except Exception:
                            pass
                if db_conn:
                    from task_analyzer.workspace_intelligence.schema_relation_builder import SchemaRelationBuilder
                    from task_analyzer.investigation.planner import load_system_map
                    smap = load_system_map()
                    tenant_names = smap.get_all_tenant_names()
                    if tenant_names:
                        tenant_db = smap.resolve_tenant_db(tenant_names[0])
                        SchemaRelationBuilder().build(index, db_conn, tenant_db)
                        logger.info("schema_relations_built_api")
        except Exception as schema_exc:
            logger.debug("schema_relations_skipped_api", error=str(schema_exc)[:100])

        stats = index.get_stats()
        duration_ms = int((_time.time() - start) * 1000)

        logger.info(
            "index_complete_api",
            repos_indexed=repos_indexed,
            classes=stats.get("classes", 0),
            duration_ms=duration_ms,
        )

        return {
            "status": "complete",
            "classes": stats.get("classes", 0),
            "methods": stats.get("methods", 0),
            "api_routes": stats.get("api_routes", 0),
            "repos_indexed": repos_indexed,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((_time.time() - start) * 1000)
        logger.error("index_failed_api", error=str(exc), duration_ms=duration_ms)
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}")


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
