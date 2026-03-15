"""
System Validation Framework — Validates all TraceAI components.

Provides a single entry point to check:
  - Claude API connectivity
  - Azure DevOps authentication (via Azure CLI)
  - Database connectivity
  - TraceAI backend health

Used by the setup helper and the VS Code extension to verify
the system is ready for investigations.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ValidationResult:
    """Result of a single validation check."""

    def __init__(self, component: str, ok: bool, message: str, details: str = ""):
        self.component = component
        self.ok = ok
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
        }


async def validate_claude_api() -> ValidationResult:
    """Test Claude API connectivity."""
    try:
        from task_analyzer.investigation.engine import _sync_anthropic_env_vars
        _sync_anthropic_env_vars()

        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return ValidationResult("Claude API", False, "API key not found",
                                    "Set ANTHROPIC_API_KEY or add to credentials.json")

        import httpx
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Say OK"}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base_url}/v1/messages", json=payload, headers=headers)

        if resp.status_code == 200:
            return ValidationResult("Claude API", True, "Connected",
                                    f"Gateway: {base_url}")
        else:
            return ValidationResult("Claude API", False, f"HTTP {resp.status_code}",
                                    resp.text[:200])
    except Exception as exc:
        return ValidationResult("Claude API", False, f"{type(exc).__name__}",
                                str(exc)[:200])


async def validate_azure_devops() -> ValidationResult:
    """Test Azure DevOps authentication via Azure CLI."""
    try:
        from task_analyzer.connectors.azure_devops.connector import _acquire_ado_token
        token = await _acquire_ado_token()
        return ValidationResult("Azure DevOps", True, "Authenticated via Azure CLI",
                                f"Token length: {len(token)}")
    except Exception as exc:
        return ValidationResult("Azure DevOps", False, f"{type(exc).__name__}",
                                str(exc)[:200])


async def validate_database() -> ValidationResult:
    """Test SQL database connectivity."""
    try:
        from task_analyzer.security.credential_manager import CredentialManager
        cm = CredentialManager()
        conn_str = cm.retrieve("sql_database", "connection_string")
        if not conn_str:
            return ValidationResult("SQL Database", False, "Not configured",
                                    "Run traceai setup to configure")

        from sqlalchemy import create_engine, text
        engine = create_engine(conn_str, connect_args={"timeout": 10})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return ValidationResult("SQL Database", True, "Connected", "")
    except ImportError as exc:
        return ValidationResult("SQL Database", False, "Driver missing",
                                str(exc)[:200])
    except Exception as exc:
        return ValidationResult("SQL Database", False, f"{type(exc).__name__}",
                                str(exc)[:200])


async def validate_backend_health() -> ValidationResult:
    """Test TraceAI backend server health."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://127.0.0.1:7420/api/health")
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            return ValidationResult("Backend Server", True, "Running on port 7420", "")
        else:
            return ValidationResult("Backend Server", False, f"HTTP {resp.status_code}",
                                    resp.text[:200])
    except Exception as exc:
        return ValidationResult("Backend Server", False, "Not running",
                                "Start with: python -m task_analyzer.api.server")


async def validate_all() -> list[ValidationResult]:
    """Run all validation checks including security safeguards."""
    # Run connectivity checks concurrently
    connectivity_results = await asyncio.gather(
        validate_claude_api(),
        validate_azure_devops(),
        validate_database(),
        validate_backend_health(),
        return_exceptions=False,
    )
    results = list(connectivity_results)

    # Run synchronous security guard checks
    results.append(validate_security_guard())
    results.append(validate_patch_path_security())
    results.append(validate_prompt_injection_guard())

    return results


def validate_security_guard() -> ValidationResult:
    """Verify the SQL SecurityGuard is active and blocking dangerous queries."""
    try:
        from task_analyzer.core.security_guard import SecurityGuard, SecurityError

        guard = SecurityGuard(safe_mode=True)

        # Verify it blocks a dangerous query
        blocked = False
        try:
            guard.validate_sql_query("DROP TABLE users")
        except SecurityError:
            blocked = True

        # Verify it blocks system metadata
        metadata_blocked = False
        try:
            guard.validate_sql_query("SELECT * FROM INFORMATION_SCHEMA.TABLES")
        except SecurityError:
            metadata_blocked = True

        if blocked and metadata_blocked and guard.sql_guard_active:
            return ValidationResult("SecurityGuard", True, "Active",
                                    "SQL write ops and system metadata blocked")
        else:
            return ValidationResult("SecurityGuard", False, "Incomplete",
                                    f"blocked={blocked}, metadata={metadata_blocked}")
    except Exception as exc:
        return ValidationResult("SecurityGuard", False, f"{type(exc).__name__}",
                                str(exc)[:200])


def validate_patch_path_security() -> ValidationResult:
    """Verify patch path traversal protection is active."""
    try:
        from task_analyzer.core.security_guard import SecurityGuard, SecurityError

        guard = SecurityGuard(safe_mode=True)

        # Verify it blocks path traversal
        traversal_blocked = False
        try:
            guard.validate_patch_path("../../.ssh/config", "/workspace")
        except SecurityError:
            traversal_blocked = True

        # Verify it blocks dotfile writes
        dotfile_blocked = False
        try:
            guard.validate_patch_path(".git/config", "/workspace")
        except SecurityError:
            dotfile_blocked = True

        if traversal_blocked and dotfile_blocked and guard.patch_path_guard_active:
            return ValidationResult("Patch Path Security", True, "Active",
                                    "Path traversal and dotfile writes blocked")
        else:
            return ValidationResult("Patch Path Security", False, "Incomplete",
                                    f"traversal={traversal_blocked}, dotfile={dotfile_blocked}")
    except Exception as exc:
        return ValidationResult("Patch Path Security", False, f"{type(exc).__name__}",
                                str(exc)[:200])


def validate_prompt_injection_guard() -> ValidationResult:
    """Verify prompt injection protection is present in the system prompt."""
    try:
        from task_analyzer.investigation.engine import INVESTIGATION_SYSTEM_PROMPT
        from task_analyzer.core.security_guard import SecurityGuard

        guard = SecurityGuard(safe_mode=True)

        has_safety = "CRITICAL SAFETY RULE" in INVESTIGATION_SYSTEM_PROMPT
        has_data_instruction = "strictly as data" in INVESTIGATION_SYSTEM_PROMPT

        if has_safety and has_data_instruction and guard.prompt_injection_guard_active:
            return ValidationResult("Prompt Injection Guard", True, "Active",
                                    "Safety instruction present in system prompt")
        else:
            return ValidationResult("Prompt Injection Guard", False, "Missing",
                                    f"safety_rule={has_safety}, data_instruction={has_data_instruction}")
    except Exception as exc:
        return ValidationResult("Prompt Injection Guard", False, f"{type(exc).__name__}",
                                str(exc)[:200])
