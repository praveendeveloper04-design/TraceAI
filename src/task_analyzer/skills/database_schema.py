"""
DatabaseSchemaSkill -- Queries and caches SQL schema metadata for investigations.

Queries INFORMATION_SCHEMA to discover tables and columns, caches the
result to ~/.traceai/db_profiles/<database>.json, and provides the
schema to Claude as investigation context.

Security: Uses a dedicated SecurityGuard bypass for schema queries only.
The INFORMATION_SCHEMA block in SecurityGuard is bypassed ONLY by this
skill using raw connector access, never by user-supplied queries.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)

DB_PROFILES_DIR = Path.home() / ".traceai" / "db_profiles"


class DatabaseSchemaSkill(BaseSkill):
    """Queries database schema and provides it as investigation context."""

    name = "database_schema"
    display_name = "Database Schema Discovery"
    description = "Discovers table and column schemas from connected databases"
    required_tools = ["DBReader"]

    async def run(self, task, context, security_guard, connectors, graph) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tables": [],
            "schema_summary": "",
        }

        start = time.time()

        try:
            # Find SQL database connector
            db_connector = None
            db_name = "unknown"
            for name, conn in connectors.items():
                conn_type = getattr(conn, "connector_type", None)
                type_val = conn_type.value if hasattr(conn_type, "value") else str(conn_type)
                if type_val == "sql_database":
                    db_connector = conn
                    db_name = getattr(conn, "_db_name", "database")
                    break

            if not db_connector:
                logger.debug("schema_skill_skipped", reason="no sql connector")
                return result

            # Check cache first
            cached = self._load_cached_schema(db_name)
            if cached:
                result["tables"] = cached.get("tables", [])
                result["schema_summary"] = cached.get("summary", "")
                logger.info("schema_loaded_from_cache", db=db_name, tables=len(result["tables"]))
                self._add_to_graph(graph, task.id, result["tables"], db_name)
                return result

            # Query schema using raw engine access (bypasses SecurityGuard
            # INFORMATION_SCHEMA block because this is a trusted system skill)
            try:
                engine = db_connector._get_engine()
                from sqlalchemy import inspect, text
                inspector = inspect(engine)

                tables_data = []
                table_names = inspector.get_table_names()[:50]

                for table_name in table_names:
                    try:
                        columns = inspector.get_columns(table_name)
                        col_info = [
                            {"name": c["name"], "type": str(c.get("type", ""))}
                            for c in columns[:20]
                        ]
                        tables_data.append({
                            "name": table_name,
                            "columns": col_info,
                        })
                    except Exception:
                        tables_data.append({"name": table_name, "columns": []})

                result["tables"] = tables_data

                # Build summary
                summary_parts = [f"Database: {db_name}", f"Tables: {len(tables_data)}"]
                for t in tables_data[:20]:
                    cols = ", ".join(c["name"] for c in t["columns"][:8])
                    summary_parts.append(f"  {t['name']}: {cols}")
                result["schema_summary"] = "\n".join(summary_parts)

                # Cache the schema
                self._save_cached_schema(db_name, {
                    "tables": tables_data,
                    "summary": result["schema_summary"],
                    "table_count": len(tables_data),
                })

                # Add to graph
                self._add_to_graph(graph, task.id, tables_data, db_name)

                engine.dispose()

            except Exception as exc:
                logger.warning("schema_query_failed", db=db_name, error=str(exc))

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "database_schema_complete",
                task_id=task.id,
                db=db_name,
                tables=len(result["tables"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("database_schema_failed", task_id=task.id, error=str(exc))

        return result

    def is_available(self, connectors: dict) -> bool:
        for conn in connectors.values():
            conn_type = getattr(conn, "connector_type", None)
            if conn_type and hasattr(conn_type, "value") and conn_type.value == "sql_database":
                return True
        return False

    @staticmethod
    def _add_to_graph(graph, task_id: str, tables: list, db_name: str) -> None:
        """Add database tables to the investigation graph."""
        db_node = f"database:{db_name}"
        graph.add_node(db_node, "database_table", {"label": db_name})
        graph.add_edge(task_id, db_node, "queries")

        for t in tables[:10]:
            table_node = f"table:{t['name']}"
            graph.add_node(table_node, "database_table", {
                "label": t["name"],
                "columns": len(t.get("columns", [])),
            })
            graph.add_edge(db_node, table_node, "contains")

    @staticmethod
    def _load_cached_schema(db_name: str) -> dict | None:
        """Load cached schema from disk."""
        cache_file = DB_PROFILES_DIR / f"{db_name}.json"
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            # Cache valid for 24 hours
            import os
            age = time.time() - os.path.getmtime(cache_file)
            if age > 86400:
                return None
            return data
        except Exception:
            return None

    @staticmethod
    def _save_cached_schema(db_name: str, data: dict) -> None:
        """Save schema to cache."""
        DB_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = DB_PROFILES_DIR / f"{db_name}.json"
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("schema_cached", db=db_name, path=str(cache_file))
