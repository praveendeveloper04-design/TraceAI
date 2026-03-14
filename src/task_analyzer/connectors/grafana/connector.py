"""
Grafana Connector — Retrieves logs and dashboard data for investigation context.

Queries Grafana's API for log data, dashboard panels, and alert information
to provide observability context during investigations.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)


class GrafanaConnector(BaseConnector):
    connector_type = ConnectorType.GRAFANA
    display_name = "Grafana"
    description = "Connect to Grafana for log retrieval and dashboard context"
    required_credentials = ["api_key"]

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._base_url = self._get_setting("base_url", "").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            api_key = self._get_credential("api_key")
            if not api_key:
                raise ValueError("Grafana API key not found in keychain")
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def validate_connection(self) -> bool:
        client = await self._get_client()
        resp = await client.get("/api/org")
        resp.raise_for_status()
        logger.info("grafana_connected", url=self._base_url)
        self._connected = True
        return True

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Search Grafana dashboards."""
        client = await self._get_client()
        resp = await client.get("/api/search", params={"query": query, "limit": 10})
        resp.raise_for_status()
        return [
            {"id": d.get("id"), "title": d.get("title"), "url": f"{self._base_url}{d.get('url', '')}", "type": d.get("type")}
            for d in resp.json()
        ]

    async def get_context(self, task: Task) -> str:
        """Search for relevant dashboards and recent alerts."""
        context_parts = []

        # Search dashboards
        dashboards = await self.search(task.title)
        if dashboards:
            context_parts.append("## Related Grafana Dashboards")
            for d in dashboards[:5]:
                context_parts.append(f"- [{d['title']}]({d['url']})")

        # Fetch recent alerts
        try:
            client = await self._get_client()
            resp = await client.get("/api/alerts", params={"state": "alerting", "limit": 10})
            if resp.status_code == 200:
                alerts = resp.json()
                if alerts:
                    context_parts.append("\n## Active Alerts")
                    for a in alerts[:5]:
                        context_parts.append(f"- **{a.get('name', 'Unknown')}**: {a.get('state', '')}")
        except Exception as exc:
            logger.debug("grafana_alerts_failed", error=str(exc))

        return "\n".join(context_parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "base_url", "prompt": "Grafana instance URL (e.g., https://grafana.example.com)", "secret": False, "required": True, "default": None},
            {"key": "api_key", "prompt": "Grafana API key (with Viewer role minimum)", "secret": True, "required": True, "default": None},
        ]
