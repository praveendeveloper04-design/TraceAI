"""
GitHub Issues Connector — Fetches issues from GitHub repositories.

Uses the GitHub REST API v3 to query issues and pull requests.
Requires a Personal Access Token with repo scope.
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


class GitHubIssuesConnector(BaseConnector):
    connector_type = ConnectorType.GITHUB_ISSUES
    display_name = "GitHub Issues"
    description = "Connect to GitHub for issue and pull request tracking"
    required_credentials = ["token"]
    optional_credentials = []

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._owner = self._get_setting("owner", "")
        self._repo = self._get_setting("repo", "")
        self._base_url = "https://api.github.com"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = self._get_credential("token")
            if not token:
                raise ValueError("GitHub token not found in keychain")
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        client = await self._get_client()
        resp = await client.get(f"/repos/{self._owner}/{self._repo}")
        resp.raise_for_status()
        logger.info("github_connected", owner=self._owner, repo=self._repo)
        self._connected = True
        return True

    async def fetch_tasks(
        self,
        assigned_to: str | None = None,
        query: str | None = None,
        max_results: int = 50,
    ) -> list[Task]:
        client = await self._get_client()
        params: dict[str, Any] = {"per_page": min(max_results, 100), "state": "open", "sort": "updated"}
        if assigned_to:
            params["assignee"] = assigned_to

        resp = await client.get(f"/repos/{self._owner}/{self._repo}/issues", params=params)
        resp.raise_for_status()

        tasks = []
        for issue in resp.json():
            if issue.get("pull_request"):
                continue  # Skip PRs
            tasks.append(self._map_issue(issue))
        return tasks[:max_results]

    async def get_task_detail(self, task_id: str) -> Task | None:
        client = await self._get_client()
        resp = await client.get(f"/repos/{self._owner}/{self._repo}/issues/{task_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        task = self._map_issue(resp.json())

        # Fetch comments
        comments_resp = await client.get(
            f"/repos/{self._owner}/{self._repo}/issues/{task_id}/comments",
            params={"per_page": 50},
        )
        if comments_resp.status_code == 200:
            for c in comments_resp.json():
                task.comments.append(TaskComment(
                    author=c.get("user", {}).get("login", "Unknown"),
                    content=c.get("body", ""),
                    created_at=_parse_date(c.get("created_at")),
                ))

        return task

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        client = await self._get_client()
        search_query = f"repo:{self._owner}/{self._repo} {query}"
        resp = await client.get("/search/issues", params={"q": search_query, "per_page": 20})
        resp.raise_for_status()
        return [self._map_issue(i).model_dump() for i in resp.json().get("items", [])]

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    def _map_issue(self, issue: dict[str, Any]) -> Task:
        labels = [l.get("name", "") for l in issue.get("labels", [])]
        return Task(
            id=f"gh-{issue['number']}",
            source=ConnectorType.GITHUB_ISSUES,
            external_id=str(issue["number"]),
            title=issue.get("title", ""),
            description=issue.get("body", "") or "",
            task_type=_infer_type(labels),
            status=TaskStatus.NEW if issue.get("state") == "open" else TaskStatus.CLOSED,
            severity=_infer_severity(labels),
            assigned_to=issue.get("assignee", {}).get("login") if issue.get("assignee") else None,
            created_by=issue.get("user", {}).get("login"),
            created_at=_parse_date(issue.get("created_at")),
            updated_at=_parse_date(issue.get("updated_at")),
            tags=labels,
            raw_data=issue,
        )

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "owner", "prompt": "GitHub repository owner (user or org)", "secret": False, "required": True, "default": None},
            {"key": "repo", "prompt": "GitHub repository name", "secret": False, "required": True, "default": None},
            {"key": "token", "prompt": "GitHub Personal Access Token (with repo scope)", "secret": True, "required": True, "default": None},
        ]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _infer_type(labels: list[str]) -> TaskType:
    lower_labels = [l.lower() for l in labels]
    if "bug" in lower_labels:
        return TaskType.BUG
    if any(l in lower_labels for l in ("feature", "enhancement")):
        return TaskType.FEATURE
    return TaskType.TASK


def _infer_severity(labels: list[str]) -> Severity:
    lower_labels = [l.lower() for l in labels]
    if any(l in lower_labels for l in ("critical", "p0", "severity: critical")):
        return Severity.CRITICAL
    if any(l in lower_labels for l in ("high", "p1", "severity: high")):
        return Severity.HIGH
    if any(l in lower_labels for l in ("medium", "p2", "severity: medium")):
        return Severity.MEDIUM
    if any(l in lower_labels for l in ("low", "p3", "severity: low")):
        return Severity.LOW
    return Severity.UNKNOWN
