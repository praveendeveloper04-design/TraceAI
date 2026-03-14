"""
MCP Connector — Integrates with Model Context Protocol servers.

Reads existing MCP configuration from the user's local setup or
allows manual configuration of MCP server endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)

# Common MCP config locations
MCP_CONFIG_PATHS = [
    Path.home() / ".config" / "mcp" / "config.json",
    Path.home() / ".mcp" / "config.json",
    Path.home() / ".vscode" / "mcp.json",
    Path.home() / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "mcp.json",
]


class McpConnector(BaseConnector):
    connector_type = ConnectorType.MCP
    display_name = "MCP (Model Context Protocol)"
    description = "Connect to MCP servers for tool access and context retrieval"
    required_credentials = []

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._servers: list[dict[str, Any]] = []
        self._client: httpx.AsyncClient | None = None

    async def validate_connection(self) -> bool:
        servers = self._get_setting("servers", [])
        if not servers:
            # Try to auto-detect from local config
            servers = self._detect_local_config()
        self._servers = servers
        logger.info("mcp_configured", server_count=len(self._servers))
        self._connected = bool(self._servers)
        return self._connected

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []  # MCP is not a task source

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Query MCP servers for relevant context."""
        results = []
        for server in self._servers:
            try:
                result = await self._query_server(server, query)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.warning("mcp_query_failed", server=server.get("name"), error=str(exc))
        return results

    async def get_context(self, task: Task) -> str:
        results = await self.search(task.title)
        if not results:
            return ""
        parts = ["## MCP Context"]
        for r in results:
            parts.append(f"- **{r.get('source', 'MCP')}**: {r.get('content', '')[:300]}")
        return "\n".join(parts)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    def _detect_local_config(self) -> list[dict[str, Any]]:
        """Attempt to read MCP configuration from known local paths."""
        for path in MCP_CONFIG_PATHS:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    servers = data.get("mcpServers", data.get("servers", []))
                    if servers:
                        logger.info("mcp_config_detected", path=str(path), count=len(servers))
                        if isinstance(servers, dict):
                            return [{"name": k, **v} for k, v in servers.items()]
                        return servers
                except Exception as exc:
                    logger.debug("mcp_config_parse_failed", path=str(path), error=str(exc))
        return []

    async def _query_server(self, server: dict[str, Any], query: str) -> dict[str, Any] | None:
        """Send a query to an MCP server endpoint."""
        url = server.get("url") or server.get("endpoint")
        if not url:
            return None
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        try:
            resp = await self._client.post(url, json={"query": query})
            if resp.status_code == 200:
                return {"source": server.get("name", url), "content": resp.text[:1000]}
        except Exception:
            pass
        return None

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "auto_detect", "prompt": "Auto-detect local MCP configuration? (yes/no)", "secret": False, "required": False, "default": "yes"},
            {"key": "server_url", "prompt": "MCP server URL (if manual configuration)", "secret": False, "required": False, "default": None},
        ]
