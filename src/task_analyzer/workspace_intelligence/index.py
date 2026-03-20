"""
Workspace Intelligence Index — Persistent SQLite-backed project knowledge.

Stores and queries:
  - Repositories and their paths
  - Code classes (controllers, services, repositories, models)
  - API routes extracted from code
  - Database tables and columns
  - Foreign key relationships between tables
  - Class-to-table mappings (which code classes reference which DB tables)

Storage: ~/.traceai/workspace_index.db

The index is built once per workspace scan and reused across investigations.
It replaces the per-investigation file scanning with pre-indexed lookups.

Security: Read-only index. Never modifies source code or databases.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

INDEX_DB_PATH = Path.home() / ".traceai" / "workspace_index.db"


# ─── Word-Boundary Matching Utility ──────────────────────────────────────────

def _is_word_boundary_match(entity: str, text: str) -> bool:
    """Check if entity appears at a word boundary in text.

    For short entities (<=4 chars), requires the entity to appear at a
    PascalCase boundary, underscore/hyphen boundary, or as a standalone word.
    This prevents false positives like "ITM" matching "RhythmService".

    For longer entities (>4 chars), plain substring match is acceptable
    since false positives are much less likely with longer strings.

    Args:
        entity: The search term (e.g., "ITM", "Trip").
        text: The text to search in (e.g., "ITMApiService.cs", "RhythmService.cs").

    Returns:
        True if entity appears at a word boundary in text.

    Examples:
        >>> _is_word_boundary_match("ITM", "ITMApiService.cs")
        True
        >>> _is_word_boundary_match("ITM", "RhythmService.cs")
        False
        >>> _is_word_boundary_match("ITM", "svc-itm-handler.cs")
        True
        >>> _is_word_boundary_match("Trip", "TripController.cs")
        True
        >>> _is_word_boundary_match("Trip", "StripService.cs")
        False
        >>> _is_word_boundary_match("Scheduling", "SchedulingEngine.cs")
        True
    """
    if not entity or not text:
        return False

    if len(entity) > 4:
        return entity.lower() in text.lower()

    # For short entities, require word-boundary matching.
    # Strategy: check if entity appears at a non-alphanumeric boundary
    # (hyphen, underscore, dot, start/end) OR at a true PascalCase boundary
    # (preceded by a lowercase letter and entity starts with uppercase).
    entity_lower = entity.lower()
    text_lower = text.lower()

    # Quick check: entity must at least be a substring
    if entity_lower not in text_lower:
        return False

    # Check non-alphanumeric boundaries (works case-insensitively)
    sep_pattern = re.compile(
        r"(?:^|(?<=[^a-zA-Z0-9]))"
        + re.escape(entity_lower)
        + r"(?:$|(?=[^a-zA-Z0-9]))",
    )
    if sep_pattern.search(text_lower):
        return True

    # Check PascalCase boundaries in the ORIGINAL text (case-sensitive).
    # Try both capitalized form (Trip) and uppercase form (ITM) since
    # the entity may arrive in any case.
    for variant in [entity[0].upper() + entity[1:], entity.upper()]:
        pascal_pattern = re.compile(
            r"(?:^|(?<=[a-z])|(?<=[^a-zA-Z0-9]))"
            + re.escape(variant)
            + r"(?:$|(?=[A-Z])|(?=[^a-zA-Z0-9]))",
        )
        if pascal_pattern.search(text):
            return True

    return False

# Skip directories during scanning
SKIP_DIRS = frozenset({
    "node_modules", "bin", "obj", "dist", "build", "__pycache__",
    ".git", "packages", "TestResults", ".vs", ".idea", ".vscode",
    "wwwroot", "Migrations", "migrations",
})

CODE_EXTENSIONS = frozenset({".cs", ".py", ".ts", ".js"})

# ── Regex patterns for code parsing ──────────────────────────────────────────

CS_CLASS = re.compile(
    r"(?:public|internal|private|protected)\s+(?:partial\s+)?class\s+(\w+)"
    r"(?:\s*:\s*([\w\s,<>]+))?",
    re.MULTILINE,
)
CS_METHOD = re.compile(
    r"(?:public|private|protected|internal|async)\s+"
    r"(?:virtual\s+|override\s+|static\s+|async\s+)*"
    r"(?:Task<[^>]+>|IActionResult|ActionResult(?:<[^>]+>)?|void|\w+)\s+"
    r"(\w+)\s*\(",
    re.MULTILINE,
)
CS_HTTP_ATTR = re.compile(
    r"\[Http(Get|Post|Put|Delete|Patch)(?:\(\"([^\"]*)\"\))?\]",
    re.MULTILINE,
)
CS_ROUTE_ATTR = re.compile(r'\[Route\("([^"]+)"\)\]', re.MULTILINE)
CS_CTOR_INJECTION = re.compile(
    r"(?:private|readonly)\s+(?:readonly\s+)?(?:I\w+)\s+_(\w+)\s*;",
    re.MULTILINE,
)
CS_DBSET = re.compile(r"DbSet<(\w+)>\s+(\w+)", re.MULTILINE)
CS_TABLE_REF = re.compile(
    r"(?:_context|_db|_repository|context)\s*\.\s*([A-Z]\w+)", re.MULTILINE
)
CS_FROM_JOIN = re.compile(
    r"\b(?:FROM|JOIN)\s+\[?([A-Z]\w{2,})\]?", re.IGNORECASE | re.MULTILINE
)

TABLE_NOISE = frozenset({
    "Add", "Remove", "Update", "Find", "Where", "Select", "First",
    "Single", "Any", "Count", "ToList", "SaveChanges", "Include",
    "Set", "Entry", "String", "Int", "Bool", "Void", "Task",
    "Object", "List", "Dictionary", "Array", "Enum", "Type",
    "Exception", "Error", "Result", "Response", "Request",
    "Logger", "Options", "Configuration", "Builder",
})


# ── Schema ───────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    scanned_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS code_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    layer TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_number INTEGER DEFAULT 0,
    base_classes TEXT DEFAULT '',
    FOREIGN KEY (repo_id) REFERENCES repositories(id)
);

CREATE TABLE IF NOT EXISTS class_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (class_id) REFERENCES code_classes(id)
);

CREATE TABLE IF NOT EXISTS class_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    dependency_name TEXT NOT NULL,
    FOREIGN KEY (class_id) REFERENCES code_classes(id)
);

CREATE TABLE IF NOT EXISTS api_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    http_method TEXT NOT NULL,
    route_path TEXT NOT NULL,
    FOREIGN KEY (class_id) REFERENCES code_classes(id)
);

CREATE TABLE IF NOT EXISTS db_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_db TEXT,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    indexed_at REAL NOT NULL,
    UNIQUE(tenant_db, schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS db_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    data_type TEXT DEFAULT '',
    ordinal INTEGER DEFAULT 0,
    FOREIGN KEY (table_id) REFERENCES db_tables(id)
);

CREATE TABLE IF NOT EXISTS db_foreign_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table_id INTEGER NOT NULL,
    source_column TEXT NOT NULL,
    target_table_id INTEGER NOT NULL,
    target_column TEXT NOT NULL,
    constraint_name TEXT DEFAULT '',
    FOREIGN KEY (source_table_id) REFERENCES db_tables(id),
    FOREIGN KEY (target_table_id) REFERENCES db_tables(id)
);

CREATE TABLE IF NOT EXISTS class_table_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    ref_type TEXT DEFAULT 'code',
    FOREIGN KEY (class_id) REFERENCES code_classes(id)
);

CREATE INDEX IF NOT EXISTS idx_classes_name ON code_classes(name);
CREATE INDEX IF NOT EXISTS idx_classes_layer ON code_classes(layer);
CREATE INDEX IF NOT EXISTS idx_classes_repo ON code_classes(repo_id);
CREATE INDEX IF NOT EXISTS idx_tables_name ON db_tables(table_name);
CREATE INDEX IF NOT EXISTS idx_tables_qualified ON db_tables(qualified_name);
CREATE INDEX IF NOT EXISTS idx_table_refs ON class_table_refs(table_name);
CREATE INDEX IF NOT EXISTS idx_fk_source ON db_foreign_keys(source_table_id);
CREATE INDEX IF NOT EXISTS idx_fk_target ON db_foreign_keys(target_table_id);
"""


# ── Workspace Index ──────────────────────────────────────────────────────────

class WorkspaceIndex:
    """
    Persistent SQLite-backed workspace intelligence index.

    Build once, query many times across investigations.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or INDEX_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Repository Indexing ──────────────────────────────────────────────

    def index_repository(self, name: str, path: str) -> int:
        """Index a repository's code structure. Returns repo_id."""
        conn = self._get_conn()
        now = time.time()

        # Upsert repository
        conn.execute(
            "INSERT INTO repositories (name, path, scanned_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET path=?, scanned_at=?",
            (name, path, now, path, now),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM repositories WHERE name=?", (name,)).fetchone()
        repo_id = row["id"]

        # Clear old data for this repo
        class_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM code_classes WHERE repo_id=?", (repo_id,)
        ).fetchall()]
        if class_ids:
            placeholders = ",".join("?" * len(class_ids))
            conn.execute(f"DELETE FROM class_methods WHERE class_id IN ({placeholders})", class_ids)
            conn.execute(f"DELETE FROM class_dependencies WHERE class_id IN ({placeholders})", class_ids)
            conn.execute(f"DELETE FROM api_routes WHERE class_id IN ({placeholders})", class_ids)
            conn.execute(f"DELETE FROM class_table_refs WHERE class_id IN ({placeholders})", class_ids)
        conn.execute("DELETE FROM code_classes WHERE repo_id=?", (repo_id,))
        conn.commit()

        # Scan repository
        repo_path = Path(path)
        files_scanned = 0
        classes_found = 0

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in CODE_EXTENSIONS:
                    continue
                full_path = Path(root) / fname
                try:
                    content = full_path.read_text(encoding="utf-8", errors="ignore")
                    rel_path = str(full_path.relative_to(repo_path))
                    files_scanned += 1

                    if ext == ".cs":
                        classes_found += self._index_csharp(conn, repo_id, content, rel_path)
                    elif ext == ".py":
                        classes_found += self._index_python(conn, repo_id, content, rel_path)
                except Exception:
                    pass

                if files_scanned % 200 == 0:
                    conn.commit()

        conn.commit()
        logger.info(
            "repository_indexed",
            repo=name,
            files=files_scanned,
            classes=classes_found,
        )
        return repo_id

    def _index_csharp(self, conn: sqlite3.Connection, repo_id: int,
                      content: str, rel_path: str) -> int:
        """Parse and index C# classes."""
        count = 0
        for match in CS_CLASS.finditer(content):
            class_name = match.group(1)
            base_classes = match.group(2) or ""
            line_num = content[:match.start()].count("\n") + 1

            layer = self._classify_layer(class_name, base_classes, content)

            cursor = conn.execute(
                "INSERT INTO code_classes (repo_id, name, layer, file_path, line_number, base_classes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (repo_id, class_name, layer, rel_path, line_num, base_classes.strip()),
            )
            class_id = cursor.lastrowid
            count += 1

            # Methods
            for m in CS_METHOD.finditer(content):
                conn.execute(
                    "INSERT INTO class_methods (class_id, name) VALUES (?, ?)",
                    (class_id, m.group(1)),
                )

            # Dependencies (constructor injection)
            for inj in CS_CTOR_INJECTION.finditer(content):
                conn.execute(
                    "INSERT INTO class_dependencies (class_id, dependency_name) VALUES (?, ?)",
                    (class_id, inj.group(1)),
                )

            # API routes
            for route_match in CS_ROUTE_ATTR.finditer(content):
                conn.execute(
                    "INSERT INTO api_routes (class_id, http_method, route_path) VALUES (?, ?, ?)",
                    (class_id, "ROUTE", route_match.group(1)),
                )
            for http_match in CS_HTTP_ATTR.finditer(content):
                conn.execute(
                    "INSERT INTO api_routes (class_id, http_method, route_path) VALUES (?, ?, ?)",
                    (class_id, http_match.group(1).upper(), http_match.group(2) or ""),
                )

            # Table references
            for dbset in CS_DBSET.finditer(content):
                tbl = dbset.group(1)
                if tbl not in TABLE_NOISE:
                    conn.execute(
                        "INSERT INTO class_table_refs (class_id, table_name, ref_type) VALUES (?, ?, ?)",
                        (class_id, tbl, "dbset"),
                    )
            for ctx_ref in CS_TABLE_REF.finditer(content):
                tbl = ctx_ref.group(1)
                if tbl not in TABLE_NOISE:
                    conn.execute(
                        "INSERT INTO class_table_refs (class_id, table_name, ref_type) VALUES (?, ?, ?)",
                        (class_id, tbl, "context"),
                    )
            for sql_ref in CS_FROM_JOIN.finditer(content):
                tbl = sql_ref.group(1)
                if tbl not in TABLE_NOISE and len(tbl) >= 3:
                    conn.execute(
                        "INSERT INTO class_table_refs (class_id, table_name, ref_type) VALUES (?, ?, ?)",
                        (class_id, tbl, "sql"),
                    )

        return count

    def _index_python(self, conn: sqlite3.Connection, repo_id: int,
                      content: str, rel_path: str) -> int:
        """Parse and index Python classes."""
        count = 0
        for match in re.finditer(r"class\s+(\w+)\s*(?:\(([^)]+)\))?:", content):
            class_name = match.group(1)
            bases = match.group(2) or ""
            line_num = content[:match.start()].count("\n") + 1

            name_lower = class_name.lower()
            if "controller" in name_lower or "view" in name_lower:
                layer = "api_controller"
            elif "service" in name_lower:
                layer = "service"
            elif "repository" in name_lower or "repo" in name_lower:
                layer = "repository"
            elif "model" in name_lower or "schema" in name_lower:
                layer = "model"
            else:
                layer = "unknown"

            cursor = conn.execute(
                "INSERT INTO code_classes (repo_id, name, layer, file_path, line_number, base_classes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (repo_id, class_name, layer, rel_path, line_num, bases.strip()),
            )
            class_id = cursor.lastrowid
            count += 1

            # Methods
            for m in re.finditer(r"def\s+(\w+)\s*\(", content):
                if not m.group(1).startswith("_"):
                    conn.execute(
                        "INSERT INTO class_methods (class_id, name) VALUES (?, ?)",
                        (class_id, m.group(1)),
                    )

            # SQLAlchemy table references
            for tbl in re.finditer(r'__tablename__\s*=\s*["\'](\w+)["\']', content):
                conn.execute(
                    "INSERT INTO class_table_refs (class_id, table_name, ref_type) VALUES (?, ?, ?)",
                    (class_id, tbl.group(1), "sqlalchemy"),
                )

        return count

    def _classify_layer(self, class_name: str, base_classes: str, content: str) -> str:
        """Classify a C# class into an application layer."""
        name_lower = class_name.lower()
        bases_lower = base_classes.lower()

        if "controller" in name_lower or "controllerbase" in bases_lower:
            return "api_controller"
        if "service" in name_lower:
            return "service"
        if "repository" in name_lower or "repo" in name_lower:
            return "repository"
        if "context" in name_lower or "dbcontext" in bases_lower:
            return "data_access"
        if "handler" in name_lower:
            return "handler"
        if "validator" in name_lower:
            return "validator"
        if "model" in name_lower or "entity" in name_lower or "dto" in name_lower:
            return "model"
        if CS_HTTP_ATTR.search(content):
            return "api_controller"
        if CS_DBSET.search(content):
            return "data_access"
        return "unknown"

    # ── Query Methods ────────────────────────────────────────────────────

    def is_indexed(self, repo_name: str) -> bool:
        """Check if a repository has been indexed."""
        conn = self._get_conn()
        row = conn.execute("SELECT id FROM repositories WHERE name=?", (repo_name,)).fetchone()
        return row is not None

    def get_repo_scan_age(self, repo_name: str) -> float | None:
        """Return seconds since last scan, or None if never scanned."""
        conn = self._get_conn()
        row = conn.execute("SELECT scanned_at FROM repositories WHERE name=?", (repo_name,)).fetchone()
        if row:
            return time.time() - row["scanned_at"]
        return None

    def find_classes_by_entity(
        self, entity: str, repo_names: list[str] | None = None,
    ) -> list[dict]:
        """Find code classes matching an entity name, with relevance scoring.

        Args:
            entity: The entity name to search for.
            repo_names: Optional list of repository names to restrict search to.
                        When provided, only classes from these repos are returned.
                        When None, searches all repos (backward compatible).

        Returns:
            List of matching classes sorted by relevance score (desc), then layer.
        """
        conn = self._get_conn()
        pattern = f"%{entity}%"

        if repo_names:
            placeholders = ",".join("?" * len(repo_names))
            rows = conn.execute(
                "SELECT c.name, c.layer, c.file_path, c.line_number, r.name as repo "
                "FROM code_classes c JOIN repositories r ON c.repo_id = r.id "
                f"WHERE c.name LIKE ? AND r.name IN ({placeholders}) "
                "ORDER BY c.layer, c.name LIMIT 50",
                (pattern, *repo_names),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT c.name, c.layer, c.file_path, c.line_number, r.name as repo "
                "FROM code_classes c JOIN repositories r ON c.repo_id = r.id "
                "WHERE c.name LIKE ? ORDER BY c.layer, c.name LIMIT 50",
                (pattern,),
            ).fetchall()

        results = [dict(r) for r in rows]
        return self._score_entity_matches(entity, results)

    def _score_entity_matches(self, entity: str, results: list[dict]) -> list[dict]:
        """Score and sort entity match results by relevance.

        Scoring tiers:
            1.0 — Exact match (class name equals entity, case-insensitive)
            0.8 — Word-boundary match (entity at PascalCase/underscore boundary)
            0.7 — Prefix match (class name starts with entity)
            0.3 — Substring match (entity embedded in class name)

        Layer priority is used as a tiebreaker within the same score.
        """
        entity_lower = entity.lower()

        # Regex for PascalCase word-boundary detection.
        # Matches entity when preceded by start-of-string, a lowercase char,
        # or a non-alpha char, and followed by end-of-string, an uppercase char,
        # or a non-alpha char.
        boundary_re = re.compile(
            r"(?:^|(?<=[a-z])|(?<=[^a-zA-Z0-9]))"
            + re.escape(entity)
            + r"(?:$|(?=[A-Z])|(?=[^a-zA-Z0-9]))",
            re.IGNORECASE,
        )

        layer_priority = {
            "api_controller": 0, "service": 1, "handler": 2,
            "repository": 3, "data_access": 4, "model": 5,
            "validator": 6, "unknown": 7,
        }

        for r in results:
            name_lower = r["name"].lower()

            if name_lower == entity_lower:
                score = 1.0
            elif name_lower.startswith(entity_lower) and boundary_re.search(r["name"]):
                score = 0.8
            elif boundary_re.search(r["name"]):
                score = 0.8
            elif name_lower.startswith(entity_lower):
                score = 0.7
            else:
                score = 0.3

            r["_sort_key"] = (-score, layer_priority.get(r.get("layer", "unknown"), 7))

        results.sort(key=lambda r: (r["_sort_key"], r["name"]))

        for r in results:
            r.pop("_sort_key", None)

        return results

    def find_classes_by_layer(self, layer: str) -> list[dict]:
        """Find all classes of a specific layer type."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT c.name, c.layer, c.file_path, r.name as repo "
            "FROM code_classes c JOIN repositories r ON c.repo_id = r.id "
            "WHERE c.layer = ? ORDER BY c.name LIMIT 100",
            (layer,),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_tables_referenced_by_class(self, class_name: str) -> list[str]:
        """Find database tables referenced by a code class."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT tr.table_name FROM class_table_refs tr "
            "JOIN code_classes c ON tr.class_id = c.id "
            "WHERE c.name = ?",
            (class_name,),
        ).fetchall()
        return [r["table_name"] for r in rows]

    def find_classes_referencing_table(
        self, table_name: str, repo_names: list[str] | None = None,
    ) -> list[dict]:
        """Find code classes that reference a database table.

        Args:
            table_name: Table name to search for.
            repo_names: Optional repo filter. When provided, only returns
                        classes from these repos.
        """
        conn = self._get_conn()
        pattern = f"%{table_name}%"

        if repo_names:
            placeholders = ",".join("?" * len(repo_names))
            rows = conn.execute(
                "SELECT c.name, c.layer, c.file_path, tr.ref_type, r.name as repo "
                "FROM class_table_refs tr "
                "JOIN code_classes c ON tr.class_id = c.id "
                "JOIN repositories r ON c.repo_id = r.id "
                f"WHERE tr.table_name LIKE ? AND r.name IN ({placeholders}) "
                "ORDER BY c.layer",
                (pattern, *repo_names),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT c.name, c.layer, c.file_path, tr.ref_type, r.name as repo "
                "FROM class_table_refs tr "
                "JOIN code_classes c ON tr.class_id = c.id "
                "JOIN repositories r ON c.repo_id = r.id "
                "WHERE tr.table_name LIKE ? ORDER BY c.layer",
                (pattern,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_api_routes(
        self, pattern: str = "", repo_names: list[str] | None = None,
    ) -> list[dict]:
        """Find API routes, optionally filtered by pattern and/or repos.

        Args:
            pattern: Search pattern for route path or class name.
            repo_names: Optional repo filter.
        """
        conn = self._get_conn()
        if pattern:
            if repo_names:
                placeholders = ",".join("?" * len(repo_names))
                rows = conn.execute(
                    "SELECT ar.http_method, ar.route_path, c.name as class_name, "
                    "c.file_path, r.name as repo "
                    "FROM api_routes ar "
                    "JOIN code_classes c ON ar.class_id = c.id "
                    "JOIN repositories r ON c.repo_id = r.id "
                    f"WHERE (ar.route_path LIKE ? OR c.name LIKE ?) "
                    f"AND r.name IN ({placeholders}) LIMIT 50",
                    (f"%{pattern}%", f"%{pattern}%", *repo_names),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ar.http_method, ar.route_path, c.name as class_name, c.file_path "
                    "FROM api_routes ar JOIN code_classes c ON ar.class_id = c.id "
                    "WHERE ar.route_path LIKE ? OR c.name LIKE ? LIMIT 50",
                    (f"%{pattern}%", f"%{pattern}%"),
                ).fetchall()
        else:
            if repo_names:
                placeholders = ",".join("?" * len(repo_names))
                rows = conn.execute(
                    "SELECT ar.http_method, ar.route_path, c.name as class_name, "
                    "c.file_path, r.name as repo "
                    "FROM api_routes ar "
                    "JOIN code_classes c ON ar.class_id = c.id "
                    "JOIN repositories r ON c.repo_id = r.id "
                    f"WHERE r.name IN ({placeholders}) LIMIT 100",
                    (*repo_names,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ar.http_method, ar.route_path, c.name as class_name, c.file_path "
                    "FROM api_routes ar JOIN code_classes c ON ar.class_id = c.id LIMIT 100",
                ).fetchall()
        return [dict(r) for r in rows]

    def find_class_dependencies(self, class_name: str) -> list[str]:
        """Find dependencies injected into a class."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT cd.dependency_name FROM class_dependencies cd "
            "JOIN code_classes c ON cd.class_id = c.id "
            "WHERE c.name = ?",
            (class_name,),
        ).fetchall()
        return [r["dependency_name"] for r in rows]

    def get_table_by_name(self, table_name: str) -> dict | None:
        """Find a database table by name."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM db_tables WHERE table_name LIKE ? LIMIT 1",
            (f"%{table_name}%",),
        ).fetchone()
        return dict(row) if row else None

    def get_fk_neighbors(self, table_name: str) -> list[dict]:
        """Find tables connected via foreign keys."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT t2.qualified_name, fk.source_column, fk.target_column "
            "FROM db_foreign_keys fk "
            "JOIN db_tables t1 ON fk.source_table_id = t1.id "
            "JOIN db_tables t2 ON fk.target_table_id = t2.id "
            "WHERE t1.table_name LIKE ? "
            "UNION "
            "SELECT t1.qualified_name, fk.source_column, fk.target_column "
            "FROM db_foreign_keys fk "
            "JOIN db_tables t1 ON fk.source_table_id = t1.id "
            "JOIN db_tables t2 ON fk.target_table_id = t2.id "
            "WHERE t2.table_name LIKE ? "
            "LIMIT 30",
            (f"%{table_name}%", f"%{table_name}%"),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_code_tables(self) -> list[str]:
        """Get all unique table names referenced in code."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT table_name FROM class_table_refs ORDER BY table_name"
        ).fetchall()
        return [r["table_name"] for r in rows]

    def get_stats(self) -> dict:
        """Get index statistics."""
        conn = self._get_conn()
        return {
            "repositories": conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0],
            "classes": conn.execute("SELECT COUNT(*) FROM code_classes").fetchone()[0],
            "methods": conn.execute("SELECT COUNT(*) FROM class_methods").fetchone()[0],
            "api_routes": conn.execute("SELECT COUNT(*) FROM api_routes").fetchone()[0],
            "db_tables": conn.execute("SELECT COUNT(*) FROM db_tables").fetchone()[0],
            "foreign_keys": conn.execute("SELECT COUNT(*) FROM db_foreign_keys").fetchone()[0],
            "class_table_refs": conn.execute("SELECT COUNT(*) FROM class_table_refs").fetchone()[0],
        }

    def summarize_for_llm(
        self, entities: list[str] | None = None,
        repo_names: list[str] | None = None,
    ) -> str:
        """Build a summary for LLM context, optionally filtered by entities and repos."""
        parts = ["## Workspace Intelligence Index"]
        stats = self.get_stats()
        parts.append(
            f"Indexed: {stats['repositories']} repos, {stats['classes']} classes, "
            f"{stats['api_routes']} routes, {stats['db_tables']} tables, "
            f"{stats['foreign_keys']} foreign keys"
        )

        if entities:
            for entity in entities[:5]:
                classes = self.find_classes_by_entity(entity, repo_names=repo_names)
                if classes:
                    parts.append(f"\n### Classes matching '{entity}'")
                    for c in classes[:5]:
                        parts.append(f"- {c['layer']}: **{c['name']}** ({c['repo']}/{c['file_path']})")

                tables_from_code = self.find_classes_referencing_table(
                    entity, repo_names=repo_names,
                )
                if tables_from_code:
                    parts.append(f"\n### Code referencing table '{entity}'")
                    for t in tables_from_code[:5]:
                        parts.append(f"- {t['layer']}: {t['name']} ({t['ref_type']})")

        return "\n".join(parts)
