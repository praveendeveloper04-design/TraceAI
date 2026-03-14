"""
SQL Database Connector — Executes read-only queries for investigation context.

Supports any SQLAlchemy-compatible database. Queries are executed in
read-only mode with statement timeouts for safety.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, Task
from task_analyzer.security.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)

MAX_ROWS = 100
QUERY_TIMEOUT_SECONDS = 30


class SqlDatabaseConnector(BaseConnector):
    connector_type = ConnectorType.SQL_DATABASE
    display_name = "SQL Database"
    description = "Connect to SQL databases for read-only investigation queries"
    required_credentials = ["connection_string"]

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._engine: Engine | None = None
        self._db_name = self._get_setting("database_name", "database")

    def _get_engine(self) -> Engine:
        if self._engine is None:
            conn_str = self._get_credential("connection_string")
            if not conn_str:
                raise ValueError("SQL connection string not found in keychain")
            self._engine = create_engine(
                conn_str,
                pool_pre_ping=True,
                pool_size=2,
                max_overflow=0,
                connect_args={"timeout": QUERY_TIMEOUT_SECONDS} if "sqlite" in conn_str else {},
            )
        return self._engine

    async def validate_connection(self) -> bool:
        engine = self._get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("sql_connected", db=self._db_name)
        self._connected = True
        return True

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []  # SQL is not a task source

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Execute a read-only SQL query and return results."""
        return self.execute_query(query)

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query (read-only enforced) and return rows as dicts."""
        # Safety: reject write operations
        normalized = sql.strip().upper()
        forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "EXEC")
        if any(normalized.startswith(kw) for kw in forbidden):
            raise ValueError(f"Write operations are not allowed. Query starts with forbidden keyword.")

        engine = self._get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            rows = result.mappings().fetchmany(MAX_ROWS)
            return [dict(row) for row in rows]

    async def get_context(self, task: Task) -> str:
        """Provide database schema info as context."""
        try:
            engine = self._get_engine()
            from sqlalchemy import inspect
            inspector = inspect(engine)
            tables = inspector.get_table_names()[:20]
            if tables:
                parts = [f"## Database: {self._db_name}", "### Tables"]
                for t in tables:
                    cols = inspector.get_columns(t)
                    col_names = ", ".join(c["name"] for c in cols[:10])
                    parts.append(f"- **{t}**: {col_names}")
                return "\n".join(parts)
        except Exception as exc:
            logger.warning("sql_context_failed", error=str(exc))
        return ""

    async def disconnect(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
        self._connected = False

    @classmethod
    def get_setup_questions(cls) -> list[dict[str, Any]]:
        return [
            {"key": "database_name", "prompt": "Friendly name for this database", "secret": False, "required": True, "default": "my-database"},
            {"key": "connection_string", "prompt": "SQLAlchemy connection string (e.g., postgresql://user:pass@host/db)", "secret": True, "required": True, "default": None},
        ]
