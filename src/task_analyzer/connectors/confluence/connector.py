"""
Confluence Connector — Searches Confluence for documentation context.

Provides supplementary context during investigations by searching
wiki pages, knowledge base articles, and runbooks.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)


class ConfluenceConnector(BaseConnector):
    connector_type = ConnectorType.CONFLUENCE
    display_name = "Confluence"
    description = "Search Confluence for documentation, runbooks, and knowledge base articles"
    required_credentials = ["email", "api_token"]

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._base_url = self._get_setting("base_url", "").rstrip("/")
        self._space_key = self._get_setting("space_key")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            email = self._get_credential("email")
            token = self._get_credential("api_token")
            if not email or not token:
                raise ValueError("Confluence credentials not found in keychain")
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=(email, token),
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        client = await self._get_client()
        resp = await client.get("/wiki/rest/api/space", params={"limit": 1})
        resp.raise_for_status()
        self._connected = True
        logger.info("confluence_connected", url=self._base_url)
        return True

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []  # Confluence is not a task source

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        client = await self._get_client()
        cql = f'text ~ "{query}"'
        if self._space_key:
            cql += f' AND space = "{self._space_key}"'

        resp = await client.get(
            "/wiki/rest/api/content/search",
            params={"cql": cql, "limit": kwargs.get("max_results", 10), "expand": "body.view"},
        )
        resp.raise_for_status()
        results = []
        for page in resp.json().get("results", []):
            results.append({
                "id": page.get("id"),
                "title": page.get("title"),
                "url": f"{self._base_url}/wiki{page.get('_links', {}).get('webui', '')}",
                "excerpt": page.get("body", {}).get("view", {}).get("value", "")[:500],
                "type": page.get("type"),
            })
        return results

    async def get_context(self, task: Task) -> str:
        """Search Confluence for pages related to the task."""
        search_terms = [task.title]
        search_terms.extend(task.tags[:3])
        results = await self.search(" ".join(search_terms), max_results=5)
        if not results:
            return ""
        parts = ["## Related Confluence Pages"]
        for r in results:
            parts.append(f"- [{r['title']}]({r['url']})")
            if r.get("excerpt"):
                parts.append(f"  {r['excerpt'][:200]}...")
        return "\n".join(parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "base_url", "prompt": "Confluence base URL (e.g., https://yourcompany.atlassian.net)", "secret": False, "required": True, "default": None},
            {"key": "space_key", "prompt": "Default space key (optional, leave blank for all spaces)", "secret": False, "required": False, "default": None},
            {"key": "email", "prompt": "Your Confluence email address", "secret": True, "required": True, "default": None},
            {"key": "api_token", "prompt": "Confluence API token", "secret": True, "required": True, "default": None},
        ]
