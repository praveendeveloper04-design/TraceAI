"""
SQL Database Connector — Executes read-only queries for investigation context.

Supports any SQLAlchemy-compatible database. Queries are executed in
read-only mode with statement timeouts for safety.

Security: All queries are validated through SecurityGuard before execution.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.core.security_guard import SecurityGuard
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

    # Class-level flag: once SQL fails, skip for the rest of this process
    _sql_unreachable: bool = False

    def __init__(self, config: ConnectorConfig, credential_manager: CredentialManager) -> None:
        super().__init__(config, credential_manager)
        self._engine: Engine | None = None
        self._db_name = self._get_setting("database_name", "database")
        self._security_guard = SecurityGuard(safe_mode=True)

    def _get_engine(self) -> Engine:
        if SqlDatabaseConnector._sql_unreachable:
            raise ConnectionError(f"SQL Server previously unreachable — skipping to avoid timeout")
        if self._engine is None:
            conn_str = self._get_credential("connection_string")
            if not conn_str:
                raise ValueError("SQL connection string not found in keychain")
            # Add connection timeout of 5 seconds for SQL Server (pyodbc)
            if "timeout" not in conn_str.lower() and "sqlite" not in conn_str.lower():
                separator = "&" if "?" in conn_str else "?"
                conn_str = f"{conn_str}{separator}connect_timeout=5"
            self._engine = create_engine(
                conn_str,
                pool_pre_ping=True,
                pool_size=2,
                max_overflow=0,
                connect_args={"timeout": 5} if "sqlite" in conn_str else {},
            )
        return self._engine

    async def validate_connection(self) -> bool:
        try:
            engine = self._get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("sql_connected", db=self._db_name)
            self._connected = True
            SqlDatabaseConnector._sql_unreachable = False
            return True
        except Exception as exc:
            SqlDatabaseConnector._sql_unreachable = True
            logger.warning("sql_unreachable", db=self._db_name, error=str(exc)[:100])
            raise

    async def fetch_tasks(self, **kwargs: Any) -> list[Task]:
        return []  # SQL is not a task source

    async def get_task_detail(self, task_id: str) -> Task | None:
        return None

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Execute a read-only SQL query and return results."""
        self._check_rate_limit()
        return self.execute_query(query)

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """
        Execute a SQL query (read-only enforced) and return rows as dicts.

        Security:
          1. SecurityGuard validates the query (keyword + object blocking)
          2. SET ROWCOUNT 100 limits result size
          3. SET LOCK_TIMEOUT 1000 prevents blocking locks
          4. fetchmany(MAX_ROWS) caps the Python-side result set
        """
        # Validate through SecurityGuard (hardened validation)
        validated_sql = self._security_guard.validate_sql_query(sql)

        engine = self._get_engine()
        with engine.connect() as conn:
            # Apply safety limits before the user query
            conn.execute(text("SET ROWCOUNT 100"))
            conn.execute(text("SET LOCK_TIMEOUT 1000"))
            result = conn.execute(text(validated_sql))
            rows = result.mappings().fetchmany(MAX_ROWS)
            return [dict(row) for row in rows]

    async def get_context(self, task: Task) -> str:
        """Provide database schema info as context."""
        self._check_rate_limit()
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
