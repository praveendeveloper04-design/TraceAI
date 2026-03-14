"""
Salesforce Connector — Queries Salesforce for customer context.

Uses the Salesforce REST API to search cases, accounts, and custom objects
for context during investigations.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)


class SalesforceConnector(BaseConnector):
    connector_type = ConnectorType.SALESFORCE
    display_name = "Salesforce"
    description = "Connect to Salesforce for customer case and account context"
    required_credentials = ["access_token", "instance_url"]

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = self._get_credential("access_token")
            instance_url = self._get_credential("instance_url")
            if not token or not instance_url:
                raise ValueError("Salesforce credentials not found in keychain")
            self._client = httpx.AsyncClient(
                base_url=instance_url.rstrip("/"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        client = await self._get_client()
        resp = await client.get("/services/data/v59.0/")
        resp.raise_for_status()
        self._connected = True
        logger.info("salesforce_connected")
        return True

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        client = await self._get_client()
        sosl = f"FIND {{{query}}} IN ALL FIELDS RETURNING Case(Id, Subject, Status, Priority, Description LIMIT 10)"
        resp = await client.get("/services/data/v59.0/search/", params={"q": sosl})
        resp.raise_for_status()
        results = []
        for record in resp.json().get("searchRecords", []):
            results.append({
                "id": record.get("Id"),
                "subject": record.get("Subject"),
                "status": record.get("Status"),
                "priority": record.get("Priority"),
                "type": record.get("attributes", {}).get("type"),
            })
        return results

    async def get_context(self, task: Task) -> str:
        results = await self.search(task.title)
        if not results:
            return ""
        parts = ["## Related Salesforce Cases"]
        for r in results[:5]:
            parts.append(f"- **{r.get('subject', 'N/A')}** (Status: {r.get('status')}, Priority: {r.get('priority')})")
        return "\n".join(parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "instance_url", "prompt": "Salesforce instance URL (e.g., https://yourorg.my.salesforce.com)", "secret": True, "required": True, "default": None},
            {"key": "access_token", "prompt": "Salesforce access token", "secret": True, "required": True, "default": None},
        ]
