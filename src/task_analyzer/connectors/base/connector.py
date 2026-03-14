"""
Base Connector — Abstract interface that every connector must implement.

The plugin architecture is intentionally simple:

1. Subclass ``BaseConnector``
2. Implement the abstract methods
3. Register via the ``ConnectorRegistry``

The core engine never imports concrete connectors directly — it discovers
them through the registry, which means new connectors can be added without
touching the investigation engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager


class BaseConnector(ABC):
    """
    Abstract base class for all Task Analyzer connectors.

    Lifecycle:
        1. ``__init__`` — receives config + credential manager
        2. ``validate_connection`` — called during setup to verify credentials
        3. ``fetch_tasks`` / ``search`` / ``get_context`` — called during investigation
        4. ``disconnect`` — cleanup
    """

    connector_type: ConnectorType
    display_name: str
    description: str
    required_credentials: list[str]  # keys the user must provide
    optional_credentials: list[str] = []

    def __init__(
        self,
        config: ConnectorConfig,
        credential_manager: CredentialManager,
    ) -> None:
        self.config = config
        self._creds = credential_manager
        self._connected = False

    # ── Abstract Methods (must be implemented) ────────────────────────────

    @abstractmethod
    async def validate_connection(self) -> bool:
        """
        Test that the connector can reach its target system.
        Returns True on success, raises on failure.
        """
        ...

    @abstractmethod
    async def fetch_tasks(
        self,
        assigned_to: str | None = None,
        query: str | None = None,
        max_results: int = 50,
    ) -> list[Task]:
        """Fetch tasks from the ticket system."""
        ...

    @abstractmethod
    async def get_task_detail(self, task_id: str) -> Task | None:
        """Fetch full details for a single task."""
        ...

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """
        Free-text search against the connector's data source.
        Returns a list of result dicts (shape varies by connector).
        """
        ...

    # ── Optional Methods (override if relevant) ──────────────────────────

    async def get_context(self, task: Task) -> str:
        """
        Return additional context for an investigation.
        Override in connectors that can provide supplementary data
        (e.g., Confluence pages, Grafana dashboards, SQL query results).
        """
        return ""

    async def disconnect(self) -> None:
        """Clean up resources. Override if the connector holds connections."""
        self._connected = False

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_credential(self, key: str) -> str | None:
        """Retrieve a credential from the OS keychain."""
        return self._creds.retrieve(self.config.name, key)

    def _get_setting(self, key: str, default: Any = None) -> Any:
        """Read a non-secret setting from the connector config."""
        return self.config.settings.get(key, default)

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        """
        Return a list of questions for the setup wizard.

        Each dict should have:
            - key: str — config key
            - prompt: str — question to display
            - secret: bool — whether to store in keychain
            - required: bool
            - default: str | None
        """
        return []
