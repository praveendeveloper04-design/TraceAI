"""
Tests for the connector base classes and registry.
"""

from __future__ import annotations

import pytest

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.base.registry import ConnectorRegistry
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager


class MockConnector(BaseConnector):
    """A mock connector for testing."""

    connector_type = ConnectorType.GITHUB_ISSUES
    display_name = "Mock Connector"
    description = "A mock connector for testing"
    required_credentials = ["token"]

    async def validate_connection(self) -> bool:
        self._connected = True
        return True

    async def fetch_tasks(self, **kwargs) -> list[Task]:
        return [
            Task(
                id="mock-1",
                source=ConnectorType.GITHUB_ISSUES,
                external_id="1",
                title="Test Task",
                description="A test task",
            )
        ]

    async def get_task_detail(self, task_id: str) -> Task | None:
        if task_id == "1":
            return Task(
                id="mock-1",
                source=ConnectorType.GITHUB_ISSUES,
                external_id="1",
                title="Test Task",
                description="A test task with details",
            )
        return None

    async def search(self, query: str, **kwargs) -> list[dict]:
        return [{"title": "Search Result", "query": query}]


class TestConnectorRegistry:
    """Tests for the ConnectorRegistry."""

    def test_register_and_create(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)
        registry.register(MockConnector)

        config = ConnectorConfig(
            connector_type=ConnectorType.GITHUB_ISSUES,
            name="test-github",
        )
        connector = registry.create(config)

        assert isinstance(connector, MockConnector)
        assert connector.config.name == "test-github"

    def test_available_types(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)
        registry.register(MockConnector)

        types = registry.available_types()
        assert len(types) == 1
        assert types[0]["type"] == "github_issues"
        assert types[0]["display_name"] == "Mock Connector"

    def test_create_unknown_type_raises(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)

        config = ConnectorConfig(
            connector_type=ConnectorType.JIRA,
            name="test-jira",
        )

        with pytest.raises(ValueError, match="No connector registered"):
            registry.create(config)

    def test_get_instance(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)
        registry.register(MockConnector)

        config = ConnectorConfig(
            connector_type=ConnectorType.GITHUB_ISSUES,
            name="test-github",
        )
        registry.create(config)

        instance = registry.get_instance("test-github")
        assert instance is not None
        assert isinstance(instance, MockConnector)

    @pytest.mark.asyncio
    async def test_fetch_tasks(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)
        registry.register(MockConnector)

        config = ConnectorConfig(
            connector_type=ConnectorType.GITHUB_ISSUES,
            name="test-github",
        )
        connector = registry.create(config)
        tasks = await connector.fetch_tasks()

        assert len(tasks) == 1
        assert tasks[0].title == "Test Task"

    @pytest.mark.asyncio
    async def test_disconnect_all(self) -> None:
        creds = CredentialManager()
        registry = ConnectorRegistry(creds)
        registry.register(MockConnector)

        config = ConnectorConfig(
            connector_type=ConnectorType.GITHUB_ISSUES,
            name="test-github",
        )
        connector = registry.create(config)
        await connector.validate_connection()
        assert connector._connected is True

        await registry.disconnect_all()
        assert len(registry.get_all_instances()) == 0
