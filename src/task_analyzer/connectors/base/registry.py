"""
Connector Registry — Dynamic discovery and instantiation of connectors.

The registry is the single point of contact between the core engine and
the connector plugins. It:

  1. Maintains a mapping of ConnectorType → connector class
  2. Instantiates connectors from saved configuration
  3. Provides the investigation engine with a list of available tools
"""

from __future__ import annotations

from typing import Any

import structlog

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)


class ConnectorRegistry:
    """
    Central registry for all connector plugins.

    Usage::

        registry = ConnectorRegistry(credential_manager)
        registry.register(AzureDevOpsConnector)
        registry.register(JiraConnector)

        # Later, instantiate from saved config:
        connector = registry.create("azure_devops", config)
    """

    def __init__(self, credential_manager: CredentialManager) -> None:
        self._creds = credential_manager
        self._registry: dict[ConnectorType, type[BaseConnector]] = {}
        self._instances: dict[str, BaseConnector] = {}

    def register(self, connector_class: type[BaseConnector]) -> None:
        """Register a connector class by its type."""
        ctype = connector_class.connector_type
        self._registry[ctype] = connector_class
        logger.debug("connector_registered", type=ctype.value, cls=connector_class.__name__)

    def create(self, config: ConnectorConfig) -> BaseConnector:
        """Instantiate a connector from configuration."""
        cls = self._registry.get(config.connector_type)
        if cls is None:
            raise ValueError(
                f"No connector registered for type '{config.connector_type.value}'. "
                f"Available: {[t.value for t in self._registry]}"
            )
        instance = cls(config=config, credential_manager=self._creds)
        self._instances[config.name] = instance
        logger.info("connector_created", type=config.connector_type.value, name=config.name)
        return instance

    def get_instance(self, name: str) -> BaseConnector | None:
        """Get an already-instantiated connector by name."""
        return self._instances.get(name)

    def get_all_instances(self) -> dict[str, BaseConnector]:
        """Return all active connector instances."""
        return dict(self._instances)

    def available_types(self) -> list[dict[str, Any]]:
        """List all registered connector types with metadata."""
        return [
            {
                "type": ctype.value,
                "display_name": cls.display_name,
                "description": cls.description,
                "required_credentials": cls.required_credentials,
            }
            for ctype, cls in self._registry.items()
        ]

    def is_registered(self, connector_type: ConnectorType) -> bool:
        return connector_type in self._registry

    def get_class(self, connector_type: ConnectorType) -> type[BaseConnector] | None:
        return self._registry.get(connector_type)

    async def disconnect_all(self) -> None:
        """Gracefully disconnect all active connectors."""
        for name, instance in self._instances.items():
            try:
                await instance.disconnect()
                logger.info("connector_disconnected", name=name)
            except Exception as exc:
                logger.error("connector_disconnect_failed", name=name, error=str(exc))
        self._instances.clear()
