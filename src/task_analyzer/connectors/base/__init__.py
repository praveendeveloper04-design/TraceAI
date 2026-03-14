"""Base connector package."""

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.base.registry import ConnectorRegistry

__all__ = ["BaseConnector", "ConnectorRegistry"]
