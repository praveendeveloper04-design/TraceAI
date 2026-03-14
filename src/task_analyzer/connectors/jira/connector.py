"""
Jira Connector — Fetches issues from Jira Cloud or Jira Server.

Uses the Jira REST API v3 (Cloud) or v2 (Server) to query issues.
Requires an API token (Cloud) or username/password (Server).
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


class JiraConnector(BaseConnector):
    connector_type = ConnectorType.JIRA
    display_name = "Jira"
    description = "Connect to Jira Cloud or Server for issue tracking"
    required_credentials = ["email", "api_token"]
    optional_credentials = []

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._base_url = self._get_setting("base_url", "").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            email = self._get_credential("email")
            token = self._get_credential("api_token")
            if not email or not token:
                raise ValueError("Jira credentials not found in keychain")
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=(email, token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        client = await self._get_client()
        resp = await client.get("/rest/api/3/myself")
        resp.raise_for_status()
        user = resp.json()
        logger.info("jira_connected", user=user.get("displayName"), url=self._base_url)
        self._connected = True
        return True

    async def fetch_tasks(
        self,
        assigned_to: str | None = None,
        query: str | None = None,
        max_results: int = 50,
    ) -> list[Task]:
        client = await self._get_client()

        jql_parts = []
        if assigned_to:
            jql_parts.append(f'assignee = "{assigned_to}"')
        if query:
            jql_parts.append(f'text ~ "{query}"')

        project = self._get_setting("project_key")
        if project:
            jql_parts.append(f'project = "{project}"')

        jql = " AND ".join(jql_parts) if jql_parts else "ORDER BY updated DESC"

        resp = await client.get(
            "/rest/api/3/search",
            params={
                "jql": jql,
                "maxResults": max_results,
                "fields": "summary,description,issuetype,status,priority,assignee,reporter,created,updated,labels,comment",
            },
        )
        resp.raise_for_status()

        tasks = []
        for issue in resp.json().get("issues", []):
            tasks.append(self._map_issue(issue))
        return tasks

    async def get_task_detail(self, task_id: str) -> Task | None:
        client = await self._get_client()
        resp = await client.get(
            f"/rest/api/3/issue/{task_id}",
            params={"expand": "renderedFields,changelog"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._map_issue(resp.json())

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        tasks = await self.fetch_tasks(query=query, max_results=kwargs.get("max_results", 20))
        return [t.model_dump() for t in tasks]

    async def get_context(self, task: Task) -> str:
        """Fetch changelog and linked issues for additional context."""
        client = await self._get_client()
        context_parts = []

        resp = await client.get(
            f"/rest/api/3/issue/{task.external_id}",
            params={"expand": "changelog", "fields": "issuelinks"},
        )
        if resp.status_code == 200:
            data = resp.json()
            changelog = data.get("changelog", {}).get("histories", [])[-10:]
            if changelog:
                context_parts.append("## Recent Changes")
                for entry in changelog:
                    for item in entry.get("items", []):
                        context_parts.append(
                            f"- {item.get('field')}: {item.get('fromString', '?')} → {item.get('toString', '?')}"
                        )

            links = data.get("fields", {}).get("issuelinks", [])
            if links:
                context_parts.append("\n## Linked Issues")
                for link in links:
                    linked = link.get("outwardIssue") or link.get("inwardIssue", {})
                    context_parts.append(
                        f"- {link.get('type', {}).get('outward', 'related')}: "
                        f"{linked.get('key', '?')} — {linked.get('fields', {}).get('summary', '')}"
                    )

        return "\n".join(context_parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    # ── Mapping ───────────────────────────────────────────────────────────

    def _map_issue(self, issue: dict[str, Any]) -> Task:
        fields = issue.get("fields", {})
        comments = []
        comment_data = fields.get("comment", {})
        if isinstance(comment_data, dict):
            for c in comment_data.get("comments", []):
                comments.append(TaskComment(
                    author=c.get("author", {}).get("displayName", "Unknown"),
                    content=_extract_adf_text(c.get("body", {})),
                    created_at=_parse_date(c.get("created")),
                ))

        description = fields.get("description", "")
        if isinstance(description, dict):
            description = _extract_adf_text(description)

        return Task(
            id=f"jira-{issue['key']}",
            source=ConnectorType.JIRA,
            external_id=issue["key"],
            title=fields.get("summary", ""),
            description=description or "",
            task_type=_map_issue_type(fields.get("issuetype", {}).get("name", "")),
            status=_map_status(fields.get("status", {}).get("name", "")),
            severity=_map_priority(fields.get("priority", {}).get("name", "")),
            assigned_to=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
            created_by=fields.get("reporter", {}).get("displayName") if fields.get("reporter") else None,
            created_at=_parse_date(fields.get("created")),
            updated_at=_parse_date(fields.get("updated")),
            tags=fields.get("labels", []),
            comments=comments,
            raw_data=issue,
        )

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "base_url", "prompt": "Jira instance URL (e.g., https://yourcompany.atlassian.net)", "secret": False, "required": True, "default": None},
            {"key": "project_key", "prompt": "Default project key (e.g., PROJ)", "secret": False, "required": False, "default": None},
            {"key": "email", "prompt": "Your Jira email address", "secret": True, "required": True, "default": None},
            {"key": "api_token", "prompt": "Jira API token", "secret": True, "required": True, "default": None},
        ]


def _extract_adf_text(adf: dict | str) -> str:
    """Extract plain text from Atlassian Document Format."""
    if isinstance(adf, str):
        return adf
    if not isinstance(adf, dict):
        return ""
    texts = []
    for node in adf.get("content", []):
        if node.get("type") == "paragraph":
            for inline in node.get("content", []):
                if inline.get("type") == "text":
                    texts.append(inline.get("text", ""))
        elif node.get("type") == "text":
            texts.append(node.get("text", ""))
    return "\n".join(texts)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _map_issue_type(name: str) -> TaskType:
    mapping = {"Bug": TaskType.BUG, "Story": TaskType.USER_STORY, "Task": TaskType.TASK, "Epic": TaskType.FEATURE, "Incident": TaskType.INCIDENT}
    return mapping.get(name, TaskType.UNKNOWN)


def _map_status(name: str) -> TaskStatus:
    lower = name.lower()
    if lower in ("to do", "open", "new", "backlog"):
        return TaskStatus.NEW
    if lower in ("in progress", "in development", "in review"):
        return TaskStatus.IN_PROGRESS
    if lower in ("done", "closed", "resolved"):
        return TaskStatus.CLOSED
    return TaskStatus.UNKNOWN


def _map_priority(name: str) -> Severity:
    lower = name.lower()
    if "highest" in lower or "critical" in lower or "blocker" in lower:
        return Severity.CRITICAL
    if "high" in lower:
        return Severity.HIGH
    if "medium" in lower:
        return Severity.MEDIUM
    if "low" in lower or "lowest" in lower:
        return Severity.LOW
    return Severity.UNKNOWN
