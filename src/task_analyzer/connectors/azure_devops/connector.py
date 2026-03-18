"""
Azure DevOps Connector — Fetches work items from Azure DevOps Services / Server.

Uses the Azure DevOps REST API to query work items, boards, and iterations.

Authentication is handled exclusively via Azure CLI (az login).
The connector acquires a Bearer token by running:

    az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798

No PATs, no Device Code, no Browser OAuth. Azure CLI is the only
supported authentication method.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime
from typing import Any

import httpx
import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import (
    ConnectorConfig,
    ConnectorType,
    Severity,
    Task,
    TaskComment,
    TaskStatus,
    TaskType,
)
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)

# Azure DevOps resource ID for token acquisition
ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"


# ─── Azure CLI Discovery ─────────────────────────────────────────────────────

def _find_az_command() -> str:
    """
    Find the Azure CLI executable.

    On Windows, 'az' may not be in PATH when invoked from VS Code or
    non-interactive shells. We check common installation paths.
    Returns the full path to az.cmd (Windows) or az (Unix).
    """
    import shutil
    import platform as _platform

    is_win = _platform.system() == "Windows"

    # Try PATH first — look for az.cmd on Windows
    if is_win:
        az = shutil.which("az.cmd") or shutil.which("az")
    else:
        az = shutil.which("az")
    if az:
        return az

    # Windows: check known install locations
    if is_win:
        candidates = [
            r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Azure CLI\wbin\az.cmd"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path

    raise FileNotFoundError(
        "Azure CLI is not installed.\n"
        "Install it from: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli\n"
        "Then run: az login"
    )


# ─── Token Acquisition (Azure CLI only) ──────────────────────────────────────

async def _acquire_ado_token() -> str:
    """
    Acquire an Azure DevOps access token via Azure CLI.

    Runs: az account get-access-token --resource 499b84ac-...

    Raises ValueError if Azure CLI is not installed or user is not logged in.
    """
    # Step 1: Find az executable
    try:
        az = _find_az_command()
    except FileNotFoundError as exc:
        raise ValueError(str(exc))

    logger.debug("azure_cli_found", path=az)

    # Step 2: Check user is logged in
    try:
        account_result = await asyncio.to_thread(
            subprocess.run,
            [az, "account", "show", "--query", "user.name", "-o", "tsv"],
            capture_output=True, text=True, timeout=15,
        )
        if account_result.returncode != 0:
            raise ValueError(
                "You are not logged in to Azure CLI.\n"
                "Please run: az login\n"
                "Then retry the operation."
            )
        user = account_result.stdout.strip()
        logger.info("azure_cli_user", user=user)
    except subprocess.TimeoutExpired:
        raise ValueError("Azure CLI timed out checking login status.")

    # Step 3: Get access token for Azure DevOps
    try:
        token_result = await asyncio.to_thread(
            subprocess.run,
            [
                az, "account", "get-access-token",
                "--resource", ADO_RESOURCE_ID,
                "--query", "accessToken",
                "-o", "tsv",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if token_result.returncode != 0:
            stderr = token_result.stderr.strip()
            raise ValueError(
                f"Failed to acquire Azure DevOps token.\n"
                f"Azure CLI error: {stderr[:200]}\n"
                f"Try: az login --allow-no-subscriptions"
            )
        token = token_result.stdout.strip()
        if not token:
            raise ValueError("Azure CLI returned an empty token.")

        logger.info(
            "azure_cli_token_acquired",
            token_length=len(token),
            user=user,
        )
        return token

    except subprocess.TimeoutExpired:
        raise ValueError("Azure CLI timed out acquiring token.")


# ─── Connector ────────────────────────────────────────────────────────────────

class AzureDevOpsConnector(BaseConnector):
    connector_type = ConnectorType.AZURE_DEVOPS
    display_name = "Azure DevOps"
    description = "Connect to Azure DevOps Services or Server for work item tracking"
    required_credentials = []  # No stored credentials — uses Azure CLI live tokens
    optional_credentials = []

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._org = self._get_setting("organization", "")
        self._project = self._get_setting("project", "")
        self._base_url = self._get_setting(
            "base_url",
            f"https://dev.azure.com/{self._org}/{self._project}",
        )
        self._org_url = f"https://dev.azure.com/{self._org}"
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    async def _acquire_token(self) -> str:
        """Acquire a fresh Azure DevOps Bearer token via Azure CLI."""
        if self._token:
            return self._token
        self._token = await _acquire_ado_token()
        return self._token

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = await self._acquire_token()
            logger.info(
                "azure_devops_auth",
                org=self._org,
                project=self._project,
                base_url=self._base_url,
                auth_method="azure_cli_bearer",
                token_length=len(token),
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        """
        Validate the connection by calling the org-level projects endpoint.

        /_apis/projects is organization-scoped — must be called against
        dev.azure.com/{org}, not dev.azure.com/{org}/{project}.
        """
        token = await self._acquire_token()
        url = f"{self._org_url}/_apis/projects?api-version=7.1"
        logger.info("azure_devops_validating", url=url)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error(
                "azure_devops_auth_failed",
                status=resp.status_code,
                body=resp.text[:500],
                url=url,
            )
        resp.raise_for_status()
        # Initialize the project-scoped client for subsequent calls
        await self._get_client()
        logger.info("azure_devops_connected", org=self._org, project=self._project)
        self._connected = True
        return True

    async def fetch_tasks(
        self,
        assigned_to: str | None = None,
        query: str | None = None,
        max_results: int = 50,
    ) -> list[Task]:
        client = await self._get_client()

        # Build WIQL query — default to @Me (current authenticated user)
        conditions = [
            "[System.TeamProject] = @project",
        ]
        if assigned_to:
            conditions.append(f"[System.AssignedTo] = '{assigned_to}'")
        else:
            # @Me resolves to the authenticated Azure CLI user
            conditions.append("[System.AssignedTo] = @Me")
        if query:
            conditions.append(f"[System.Title] CONTAINS '{query}'")

        wiql = (
            "SELECT [System.Id], [System.Title], [System.State], "
            "[System.WorkItemType], [System.AssignedTo] "
            f"FROM WorkItems WHERE {' AND '.join(conditions)} "
            "ORDER BY [System.ChangedDate] DESC"
        )

        resp = await client.post(
            "/_apis/wit/wiql?api-version=7.1",
            json={"query": wiql},
        )
        resp.raise_for_status()
        work_item_refs = resp.json().get("workItems", [])[:max_results]

        if not work_item_refs:
            return []

        ids = ",".join(str(wi["id"]) for wi in work_item_refs)
        fields = "System.Id,System.Title,System.Description,System.WorkItemType,System.State,System.AssignedTo,System.CreatedBy,System.CreatedDate,System.ChangedDate,System.Tags,Microsoft.VSTS.Common.Severity"
        resp = await client.get(
            f"/_apis/wit/workitems?ids={ids}&fields={fields}&api-version=7.1"
        )
        resp.raise_for_status()

        tasks = []
        for wi in resp.json().get("value", []):
            tasks.append(self._map_work_item(wi))
        return tasks

    async def get_task_detail(self, task_id: str) -> Task | None:
        client = await self._get_client()
        resp = await client.get(
            f"/_apis/wit/workitems/{task_id}?$expand=all&api-version=7.1"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        wi = resp.json()

        task = self._map_work_item(wi)

        comments_resp = await client.get(
            f"/_apis/wit/workitems/{task_id}/comments?api-version=7.1-preview.4"
        )
        if comments_resp.status_code == 200:
            for c in comments_resp.json().get("comments", []):
                task.comments.append(TaskComment(
                    author=c.get("createdBy", {}).get("displayName", "Unknown"),
                    content=c.get("text", ""),
                    created_at=_parse_date(c.get("createdDate")),
                ))

        return task

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        tasks = await self.fetch_tasks(query=query, max_results=kwargs.get("max_results", 20))
        return [t.model_dump() for t in tasks]

    async def get_context(self, task: Task) -> str:
        """Fetch related work items and recent history for context."""
        client = await self._get_client()
        context_parts = []

        resp = await client.get(
            f"/_apis/wit/workitems/{task.external_id}/updates?api-version=7.1"
        )
        if resp.status_code == 200:
            updates = resp.json().get("value", [])[-10:]
            if updates:
                context_parts.append("## Recent History")
                for u in updates:
                    fields = u.get("fields", {})
                    if "System.State" in fields:
                        old = fields["System.State"].get("oldValue", "?")
                        new = fields["System.State"].get("newValue", "?")
                        context_parts.append(f"- State changed: {old} -> {new}")

        return "\n".join(context_parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._token = None
        self._connected = False

    # ── Mapping Helpers ───────────────────────────────────────────────────

    def _map_work_item(self, wi: dict[str, Any]) -> Task:
        fields = wi.get("fields", {})
        return Task(
            id=f"ado-{wi['id']}",
            source=ConnectorType.AZURE_DEVOPS,
            external_id=str(wi["id"]),
            title=fields.get("System.Title", ""),
            description=fields.get("System.Description", ""),
            task_type=_map_work_item_type(fields.get("System.WorkItemType", "")),
            status=_map_status(fields.get("System.State", "")),
            severity=_map_severity(fields.get("Microsoft.VSTS.Common.Severity", "")),
            assigned_to=_extract_identity(fields.get("System.AssignedTo")),
            created_by=_extract_identity(fields.get("System.CreatedBy")),
            created_at=_parse_date(fields.get("System.CreatedDate")),
            updated_at=_parse_date(fields.get("System.ChangedDate")),
            tags=[t.strip() for t in fields.get("System.Tags", "").split(";") if t.strip()],
            raw_data=wi,
        )

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {
                "key": "organization",
                "prompt": "Azure DevOps organization name",
                "secret": False,
                "required": True,
                "default": None,
            },
            {
                "key": "project",
                "prompt": "Azure DevOps project name",
                "secret": False,
                "required": True,
                "default": None,
            },
        ]


# ── Utility Functions ─────────────────────────────────────────────────────────

def _extract_identity(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("uniqueName")
    return str(value) if value else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _map_work_item_type(wit: str) -> TaskType:
    mapping = {
        "Bug": TaskType.BUG,
        "User Story": TaskType.USER_STORY,
        "Task": TaskType.TASK,
        "Feature": TaskType.FEATURE,
        "Incident": TaskType.INCIDENT,
    }
    return mapping.get(wit, TaskType.UNKNOWN)


def _map_status(state: str) -> TaskStatus:
    mapping = {
        "New": TaskStatus.NEW,
        "Active": TaskStatus.ACTIVE,
        "In Progress": TaskStatus.IN_PROGRESS,
        "Resolved": TaskStatus.RESOLVED,
        "Closed": TaskStatus.CLOSED,
        "Done": TaskStatus.CLOSED,
    }
    return mapping.get(state, TaskStatus.UNKNOWN)


def _map_severity(sev: str) -> Severity:
    sev_lower = str(sev).lower()
    if "1" in sev_lower or "critical" in sev_lower:
        return Severity.CRITICAL
    if "2" in sev_lower or "high" in sev_lower:
        return Severity.HIGH
    if "3" in sev_lower or "medium" in sev_lower:
        return Severity.MEDIUM
    if "4" in sev_lower or "low" in sev_lower:
        return Severity.LOW
    return Severity.UNKNOWN
