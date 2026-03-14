"""
Connectors package — Plugin-style connector system for Task Analyzer.

All connectors implement the ``BaseConnector`` interface and are registered
with the ``ConnectorRegistry`` at startup. The registry provides dynamic
discovery so new connectors can be added without modifying the core engine.

Connector Types:
    - **Ticket Sources** (mandatory, one required):
        Azure DevOps, Jira, GitHub Issues
    - **Context Providers** (optional):
        Confluence, Salesforce, SQL Database, MCP, Grafana
"""

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.base.registry import ConnectorRegistry

# Import all concrete connectors for registration
from task_analyzer.connectors.azure_devops.connector import AzureDevOpsConnector
from task_analyzer.connectors.jira.connector import JiraConnector
from task_analyzer.connectors.github_issues.connector import GitHubIssuesConnector
from task_analyzer.connectors.confluence.connector import ConfluenceConnector
from task_analyzer.connectors.salesforce.connector import SalesforceConnector
from task_analyzer.connectors.sql_database.connector import SqlDatabaseConnector
from task_analyzer.connectors.mcp.connector import McpConnector
from task_analyzer.connectors.grafana.connector import GrafanaConnector

# All available connector classes
ALL_CONNECTORS: list[type[BaseConnector]] = [
    AzureDevOpsConnector,
    JiraConnector,
    GitHubIssuesConnector,
    ConfluenceConnector,
    SalesforceConnector,
    SqlDatabaseConnector,
    McpConnector,
    GrafanaConnector,
]

# Ticket source connectors (user must configure at least one)
TICKET_CONNECTORS: list[type[BaseConnector]] = [
    AzureDevOpsConnector,
    JiraConnector,
    GitHubIssuesConnector,
]

# Optional context connectors
CONTEXT_CONNECTORS: list[type[BaseConnector]] = [
    ConfluenceConnector,
    SalesforceConnector,
    SqlDatabaseConnector,
    McpConnector,
    GrafanaConnector,
]


def create_default_registry(credential_manager=None) -> ConnectorRegistry:
    """Create a registry with all built-in connectors registered."""
    from task_analyzer.security.credential_manager import credential_manager as default_cm

    cm = credential_manager or default_cm
    registry = ConnectorRegistry(cm)
    for cls in ALL_CONNECTORS:
        registry.register(cls)
    return registry


__all__ = [
    "BaseConnector",
    "ConnectorRegistry",
    "AzureDevOpsConnector",
    "JiraConnector",
    "GitHubIssuesConnector",
    "ConfluenceConnector",
    "SalesforceConnector",
    "SqlDatabaseConnector",
    "McpConnector",
    "GrafanaConnector",
    "ALL_CONNECTORS",
    "TICKET_CONNECTORS",
    "CONTEXT_CONNECTORS",
    "create_default_registry",
]
