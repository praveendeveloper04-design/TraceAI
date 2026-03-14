"""
Azure DevOps Connector — Fetches work items from Azure DevOps Services / Server.

Uses the Azure DevOps REST API to query work items, boards, and iterations.
Requires a Personal Access Token (PAT) with Work Items (Read) scope.
"""

from __future__ import annotations

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


class AzureDevOpsConnector(BaseConnector):
    connector_type = ConnectorType.AZURE_DEVOPS
    display_name = "Azure DevOps"
    description = "Connect to Azure DevOps Services or Server for work item tracking"
    required_credentials = ["pat"]
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

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            pat = self._get_credential("pat")
            if not pat:
                raise ValueError(
                    "Azure DevOps PAT not found. Checked: OS keychain, "
                    "~/.traceai/credentials.json, config.json settings. "
                    "Please run 'traceai setup' or create credentials.json."
                )
            logger.info(
                "azure_devops_auth",
                org=self._org,
                project=self._project,
                base_url=self._base_url,
                pat_length=len(pat),
                pat_prefix=pat[:4] + "..." if len(pat) > 4 else "****",
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=("", pat),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        """
        Validate the connection by calling the org-level projects endpoint.

        Note: /_apis/projects is an organization-scoped endpoint. It must be
        called against the org URL (dev.azure.com/{org}), NOT the project URL
        (dev.azure.com/{org}/{project}). Calling it with the project in the
        path returns 401 on Azure DevOps Services.

        We use a separate one-shot request here because the main client's
        base_url is project-scoped (which is correct for WIQL and work item
        APIs), but the projects endpoint lives at the org level.
        """
        pat = self._get_credential("pat")
        if not pat:
            raise ValueError(
                "Azure DevOps PAT not found. Checked: OS keychain, "
                "~/.traceai/credentials.json, config.json settings."
            )
        url = f"{self._org_url}/_apis/projects?api-version=7.1"
        logger.info("azure_devops_validating", url=url)
        async with httpx.AsyncClient(auth=("", pat), timeout=30.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.error(
                "azure_devops_auth_failed",
                status=resp.status_code,
                body=resp.text[:500],
                url=url,
            )
        resp.raise_for_status()
        # Now initialize the project-scoped client for all subsequent calls
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

        # Build WIQL query
        conditions = ["[System.TeamProject] = @project"]
        if assigned_to:
            conditions.append(f"[System.AssignedTo] = '{assigned_to}'")
        if query:
            conditions.append(f"[System.Title] CONTAINS '{query}'")

        wiql = f"SELECT [System.Id] FROM WorkItems WHERE {' AND '.join(conditions)} ORDER BY [System.ChangedDate] DESC"

        resp = await client.post(
            "/_apis/wit/wiql?api-version=7.1",
            json={"query": wiql},
        )
        resp.raise_for_status()
        work_item_refs = resp.json().get("workItems", [])[:max_results]

        if not work_item_refs:
            return []

        # Batch fetch work item details
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

        # Fetch comments
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

        # Get work item updates (history)
        resp = await client.get(
            f"/_apis/wit/workitems/{task.external_id}/updates?api-version=7.1"
        )
        if resp.status_code == 200:
            updates = resp.json().get("value", [])[-10:]  # last 10 updates
            if updates:
                context_parts.append("## Recent History")
                for u in updates:
                    fields = u.get("fields", {})
                    if "System.State" in fields:
                        old = fields["System.State"].get("oldValue", "?")
                        new = fields["System.State"].get("newValue", "?")
                        context_parts.append(f"- State changed: {old} → {new}")

        return "\n".join(context_parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
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
            {
                "key": "pat",
                "prompt": "Personal Access Token (PAT) with Work Items Read scope",
                "secret": True,
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
