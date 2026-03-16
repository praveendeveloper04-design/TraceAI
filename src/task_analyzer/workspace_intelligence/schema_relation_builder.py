"""
Schema Relation Builder — Discovers and indexes database relationships.

Queries INFORMATION_SCHEMA to build a complete picture of:
  - All tables and their columns
  - Foreign key relationships (Table → FK → Table)
  - Column data types and ordinal positions

Stores everything in the workspace intelligence index (SQLite).

Security: All queries validated through SecurityGuard with
allow_schema_inspection=True. Only SELECT queries are executed.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from task_analyzer.core.security_guard import SecurityGuard

logger = structlog.get_logger(__name__)


class SchemaRelationBuilder:
    """
    Builds a complete database relationship graph from INFORMATION_SCHEMA.

    Discovers:
      1. All tables with schema qualification
      2. All columns with data types
      3. All foreign key constraints (source → target)

    Stores results in the WorkspaceIndex SQLite database.
    """

    def __init__(self) -> None:
        self._guard = SecurityGuard(safe_mode=True)

    def build(
        self,
        workspace_index,
        db_connector: Any,
        tenant_db: str | None = None,
    ) -> dict[str, int]:
        """
        Discover schema and foreign keys, store in workspace index.

        Args:
            workspace_index: WorkspaceIndex instance
            db_connector: SQL database connector
            tenant_db: Optional tenant database name for cross-DB queries

        Returns:
            Stats dict with counts of tables, columns, foreign_keys indexed.
        """
        start = time.time()
        stats = {"tables": 0, "columns": 0, "foreign_keys": 0}

        try:
            engine = db_connector._get_engine()
            from sqlalchemy import text

            conn = workspace_index._get_conn()

            # Step 1: Discover all tables
            tables_indexed = self._index_tables(conn, engine, tenant_db)
            stats["tables"] = tables_indexed

            # Step 2: Discover columns for each table
            columns_indexed = self._index_columns(conn, engine, tenant_db)
            stats["columns"] = columns_indexed

            # Step 3: Discover foreign key relationships
            fks_indexed = self._index_foreign_keys(conn, engine, tenant_db)
            stats["foreign_keys"] = fks_indexed

            conn.commit()

            elapsed = int((time.time() - start) * 1000)
            logger.info(
                "schema_relations_built",
                tenant_db=tenant_db,
                tables=stats["tables"],
                columns=stats["columns"],
                foreign_keys=stats["foreign_keys"],
                elapsed_ms=elapsed,
            )

        except Exception as exc:
            logger.warning("schema_relation_build_failed", error=str(exc)[:200])

        return stats

    def _index_tables(self, conn, engine, tenant_db: str | None) -> int:
        """Discover and index all tables."""
        from sqlalchemy import text as sa_text

        if not tenant_db:
            return 0

        query = (
            f"SELECT TABLE_SCHEMA, TABLE_NAME "
            f"FROM {tenant_db}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_TYPE='BASE TABLE' "
            f"ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        validated = self._guard.validate_sql_query(query, allow_schema_inspection=True)

        now = time.time()
        count = 0

        with engine.connect() as db_conn:
            db_conn.execute(sa_text("SET ROWCOUNT 1000"))
            rows = db_conn.execute(sa_text(validated))

            for row in rows:
                schema_name = row[0]
                table_name = row[1]
                qualified = f"{schema_name}.{table_name}"

                conn.execute(
                    "INSERT INTO db_tables (tenant_db, schema_name, table_name, qualified_name, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(tenant_db, schema_name, table_name) DO UPDATE SET indexed_at=?",
                    (tenant_db, schema_name, table_name, qualified, now, now),
                )
                count += 1

        conn.commit()
        return count

    def _index_columns(self, conn, engine, tenant_db: str | None) -> int:
        """Discover and index columns for all indexed tables."""
        from sqlalchemy import text as sa_text

        if not tenant_db:
            return 0

        # Get all table IDs we just indexed
        table_rows = conn.execute(
            "SELECT id, schema_name, table_name FROM db_tables WHERE tenant_db=?",
            (tenant_db,),
        ).fetchall()

        if not table_rows:
            return 0

        # Clear old columns
        table_ids = [r["id"] for r in table_rows]
        placeholders = ",".join("?" * len(table_ids))
        conn.execute(f"DELETE FROM db_columns WHERE table_id IN ({placeholders})", table_ids)

        query = (
            f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION "
            f"FROM {tenant_db}.INFORMATION_SCHEMA.COLUMNS "
            f"ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
        validated = self._guard.validate_sql_query(query, allow_schema_inspection=True)

        count = 0
        # Build lookup: (schema, table) -> table_id
        table_lookup = {(r["schema_name"], r["table_name"]): r["id"] for r in table_rows}

        with engine.connect() as db_conn:
            db_conn.execute(sa_text("SET ROWCOUNT 50000"))
            rows = db_conn.execute(sa_text(validated))

            for row in rows:
                schema_name = row[0]
                table_name = row[1]
                col_name = row[2]
                data_type = row[3]
                ordinal = row[4]

                table_id = table_lookup.get((schema_name, table_name))
                if table_id:
                    conn.execute(
                        "INSERT INTO db_columns (table_id, column_name, data_type, ordinal) "
                        "VALUES (?, ?, ?, ?)",
                        (table_id, col_name, data_type, ordinal),
                    )
                    count += 1

        conn.commit()
        return count

    def _index_foreign_keys(self, conn, engine, tenant_db: str | None) -> int:
        """Discover and index foreign key relationships."""
        from sqlalchemy import text as sa_text

        if not tenant_db:
            return 0

        # Clear old FKs for this tenant
        conn.execute(
            "DELETE FROM db_foreign_keys WHERE source_table_id IN "
            "(SELECT id FROM db_tables WHERE tenant_db=?)",
            (tenant_db,),
        )

        query = (
            f"SELECT "
            f"fk.TABLE_SCHEMA AS src_schema, fk.TABLE_NAME AS src_table, "
            f"cu.COLUMN_NAME AS src_column, "
            f"pk.TABLE_SCHEMA AS tgt_schema, pk.TABLE_NAME AS tgt_table, "
            f"pt.COLUMN_NAME AS tgt_column, "
            f"rc.CONSTRAINT_NAME "
            f"FROM {tenant_db}.INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc "
            f"JOIN {tenant_db}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS fk "
            f"ON rc.CONSTRAINT_NAME = fk.CONSTRAINT_NAME "
            f"AND rc.CONSTRAINT_SCHEMA = fk.CONSTRAINT_SCHEMA "
            f"JOIN {tenant_db}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk "
            f"ON rc.UNIQUE_CONSTRAINT_NAME = pk.CONSTRAINT_NAME "
            f"AND rc.UNIQUE_CONSTRAINT_SCHEMA = pk.CONSTRAINT_SCHEMA "
            f"JOIN {tenant_db}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE cu "
            f"ON fk.CONSTRAINT_NAME = cu.CONSTRAINT_NAME "
            f"AND fk.CONSTRAINT_SCHEMA = cu.CONSTRAINT_SCHEMA "
            f"JOIN {tenant_db}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE pt "
            f"ON pk.CONSTRAINT_NAME = pt.CONSTRAINT_NAME "
            f"AND pk.CONSTRAINT_SCHEMA = pt.CONSTRAINT_SCHEMA"
        )
        validated = self._guard.validate_sql_query(query, allow_schema_inspection=True)

        # Build lookup: (schema, table) -> table_id
        table_rows = conn.execute(
            "SELECT id, schema_name, table_name FROM db_tables WHERE tenant_db=?",
            (tenant_db,),
        ).fetchall()
        table_lookup = {(r["schema_name"], r["table_name"]): r["id"] for r in table_rows}

        count = 0
        try:
            with engine.connect() as db_conn:
                db_conn.execute(sa_text("SET ROWCOUNT 5000"))
                db_conn.execute(sa_text("SET LOCK_TIMEOUT 10000"))
                rows = db_conn.execute(sa_text(validated))

                for row in rows:
                    src_id = table_lookup.get((row[0], row[1]))
                    tgt_id = table_lookup.get((row[3], row[4]))

                    if src_id and tgt_id:
                        conn.execute(
                            "INSERT INTO db_foreign_keys "
                            "(source_table_id, source_column, target_table_id, target_column, constraint_name) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (src_id, row[2], tgt_id, row[5], row[6] or ""),
                        )
                        count += 1

        except Exception as exc:
            logger.debug("fk_indexing_partial", error=str(exc)[:100])

        conn.commit()
        return count
