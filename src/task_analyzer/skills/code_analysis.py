"""
CodeAnalysisSkill -- Discovers SQL tables and code flows from repository source code.

Instead of fuzzy-matching entity names against database schema, this skill
searches the actual application code to find which tables are used by the
feature being investigated.

Discovery approach:
  1. Search repo files for entity references (TripController, DeleteTrip, etc.)
  2. Read matched files and extract code patterns (Controller -> Service -> Repository)
  3. Extract SQL table names from code (context.Trips, FROM Trips, _db.Trips)
  4. Return discovered tables, code flows, and relevant file paths

This produces higher-quality table lists than schema fuzzy matching because
it only returns tables that the application actually uses for the feature.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)

# File extensions to search
CODE_EXTENSIONS = {
    ".cs", ".py", ".ts", ".js", ".java", ".go", ".rs", ".rb",
    ".sql", ".xml", ".json", ".yaml", ".yml",
}

# Application layer patterns (class/file name patterns)
LAYER_PATTERNS = {
    "controller": re.compile(r"(\w+Controller)\b", re.IGNORECASE),
    "service": re.compile(r"(\w+Service)\b", re.IGNORECASE),
    "repository": re.compile(r"(\w+Repository)\b", re.IGNORECASE),
    "handler": re.compile(r"(\w+Handler)\b", re.IGNORECASE),
    "manager": re.compile(r"(\w+Manager)\b", re.IGNORECASE),
    "dbcontext": re.compile(r"(\w+(?:DbContext|Context))\b", re.IGNORECASE),
    "dao": re.compile(r"(\w+(?:DAO|Dao))\b", re.IGNORECASE),
}

# Patterns that reference SQL tables in code
TABLE_REFERENCE_PATTERNS = [
    # C# Entity Framework: context.Trips, _db.Trips, DbSet<Trip>
    re.compile(r"(?:context|_db|_context|dbContext|_dbContext)\s*\.\s*(\w+)", re.IGNORECASE),
    re.compile(r"DbSet\s*<\s*(\w+)\s*>", re.IGNORECASE),
    re.compile(r"Set\s*<\s*(\w+)\s*>", re.IGNORECASE),
    # SQL in code: FROM Table, JOIN Table, INTO Table, UPDATE Table
    re.compile(r"\bFROM\s+\[?(\w+)\]?", re.IGNORECASE),
    re.compile(r"\bJOIN\s+\[?(\w+)\]?", re.IGNORECASE),
    re.compile(r"\bINTO\s+\[?(\w+)\]?", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+\[?(\w+)\]?", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+\[?(\w+)\]?", re.IGNORECASE),
    # Table name in string: "Trips", 'Trips', [Trips]
    re.compile(r'"\[?(\w{3,})\]?"', re.IGNORECASE),
    # C# model/entity class mapping: [Table("Trips")]
    re.compile(r'\[Table\s*\(\s*"(\w+)"\s*\)\]', re.IGNORECASE),
    # Python SQLAlchemy: __tablename__ = "trips"
    re.compile(r'__tablename__\s*=\s*["\'](\w+)["\']', re.IGNORECASE),
]

# Words that look like table names but aren't
TABLE_NOISE = frozenset({
    "string", "int", "bool", "void", "null", "true", "false", "var",
    "class", "public", "private", "static", "async", "await", "return",
    "this", "base", "new", "get", "set", "value", "key", "name",
    "type", "list", "dict", "map", "array", "object", "task",
    "action", "result", "model", "view", "data", "item", "entity",
    "config", "options", "settings", "logger", "context", "service",
    "controller", "repository", "handler", "manager", "factory",
    "interface", "abstract", "override", "virtual", "readonly",
    "select", "where", "order", "group", "having", "limit",
    "from", "join", "into", "update", "delete", "insert", "create",
    "table", "column", "index", "schema", "database",
})


class CodeAnalysisSkill(BaseSkill):
    """Discovers SQL tables and code flows from repository source code."""

    name = "code_analysis"
    display_name = "Code-Driven SQL Discovery"
    description = "Searches repository code to find which SQL tables are used by the investigated feature"
    required_tools = ["RepoReader"]

    async def run(self, task, context, security_guard, connectors, graph) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code_tables": [],
            "code_flows": [],
            "relevant_files": [],
            "code_references": [],
        }

        start = time.time()

        try:
            security_guard.validate_tool("RepoReader", "read_file")

            # Get entities from the investigation plan or extract from task
            plan = context.get("investigation_plan")
            if plan and hasattr(plan, "entities"):
                entities = plan.entities
            else:
                entities = self._extract_entities(task.title, task.description)

            if not entities:
                return result

            # Get repo paths from profiles
            profiles = context.get("profiles", [])
            repo_paths = []
            for p in profiles:
                rp = getattr(p, "repo_path", None)
                if rp and Path(rp).exists():
                    repo_paths.append(Path(rp))

            if not repo_paths:
                logger.debug("code_analysis_skipped", reason="no repo paths")
                return result

            # Step 1: Search repos for files matching entities
            matched_files = self._search_files(repo_paths, entities)
            result["relevant_files"] = [str(f) for f in matched_files[:20]]

            # Step 2: Read matched files and extract code patterns
            all_tables: set[str] = set()
            all_flows: list[dict] = []
            all_refs: list[dict] = []

            for file_path in matched_files[:15]:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")

                    # Extract table references
                    tables = self._extract_tables_from_code(content)
                    all_tables.update(tables)

                    # Extract code flow patterns
                    flows = self._extract_code_flows(content, file_path.name)
                    all_flows.extend(flows)

                    # Extract entity references with line numbers
                    refs = self._extract_references(content, entities, file_path)
                    all_refs.extend(refs)

                except Exception as exc:
                    logger.debug("file_read_failed", file=str(file_path), error=str(exc))

            result["code_tables"] = sorted(all_tables)
            result["code_flows"] = all_flows[:20]
            result["code_references"] = all_refs[:30]

            # Add to graph
            for table in result["code_tables"][:10]:
                node_id = f"table:{table}"
                graph.add_node(node_id, "database_table", {"label": table, "source": "code_analysis"})
                graph.add_edge(task.id, node_id, "references")

            for ref in result["code_references"][:10]:
                file_node = f"file:{ref['file']}"
                graph.add_node(file_node, "file", {"label": ref["file"]})
                graph.add_edge(task.id, file_node, "references")

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "code_analysis_complete",
                task_id=task.id,
                tables=len(result["code_tables"]),
                files=len(result["relevant_files"]),
                flows=len(result["code_flows"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("code_analysis_failed", task_id=task.id, error=str(exc))

        return result

    # ── File Search ───────────────────────────────────────────────────────

    def _search_files(self, repo_paths: list[Path], entities: list[str]) -> list[Path]:
        """Search repos for files whose names or content match entities."""
        matched: list[Path] = []
        entity_patterns = [e.lower() for e in entities if len(e) >= 3]

        for repo_path in repo_paths:
            for root, dirs, files in os.walk(repo_path):
                # Skip hidden dirs, build dirs, node_modules
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in {
                        "node_modules", "bin", "obj", "dist", "build",
                        "__pycache__", ".git", "packages", "TestResults",
                    }
                ]

                for fname in files:
                    ext = Path(fname).suffix.lower()
                    if ext not in CODE_EXTENSIONS:
                        continue

                    fname_lower = fname.lower()
                    # Check if filename matches any entity
                    for entity in entity_patterns:
                        if self._is_entity_filename_match(entity, fname, fname_lower):
                            matched.append(Path(root) / fname)
                            break

                if len(matched) >= 50:
                    break

        return matched

    @staticmethod
    def _is_entity_filename_match(entity: str, filename: str, filename_lower: str) -> bool:
        """Match entity against filename with word-boundary awareness for short entities.

        For entities <=4 chars, requires the entity to appear at a word boundary
        (start of name, after hyphen/underscore/dot, or at a PascalCase boundary).
        This prevents short acronyms from matching embedded substrings in unrelated files.

        For longer entities (>4 chars), plain substring match is used.

        Args:
            entity: The search term (lowercase).
            filename: Original filename preserving case (for PascalCase detection).
            filename_lower: Lowercased filename (for substring checks).
        """
        if not entity or not filename_lower:
            return False

        if len(entity) > 4:
            return entity in filename_lower

        if entity not in filename_lower:
            return False

        # Check non-alphanumeric boundaries (hyphen, underscore, dot, start/end)
        sep_pattern = re.compile(
            r"(?:^|(?<=[^a-zA-Z0-9]))"
            + re.escape(entity)
            + r"(?:$|(?=[^a-zA-Z0-9]))",
        )
        if sep_pattern.search(filename_lower):
            return True

        # Check PascalCase boundaries in original filename (case-sensitive).
        # Try both capitalized (Trip) and uppercase (ITM) forms.
        for variant in [entity[0].upper() + entity[1:], entity.upper()]:
            pascal_pattern = re.compile(
                r"(?:^|(?<=[a-z])|(?<=[^a-zA-Z0-9]))"
                + re.escape(variant)
                + r"(?:$|(?=[A-Z])|(?=[^a-zA-Z0-9]))",
            )
            if pascal_pattern.search(filename):
                return True

        return False

    # ── Table Extraction ──────────────────────────────────────────────────

    def _extract_tables_from_code(self, content: str) -> set[str]:
        """Extract SQL table names referenced in code."""
        tables: set[str] = set()

        # Pattern 1: C# Entity Framework DbSet<Entity>
        for m in re.finditer(r"DbSet\s*<\s*(\w+)\s*>", content):
            tables.add(m.group(1))

        # Pattern 2: context.TableName or _db.TableName (property access on db context)
        for m in re.finditer(r"(?:context|_db|_context|dbContext|_dbContext|_repository)\s*\.\s*([A-Z]\w+)", content):
            name = m.group(1)
            if name not in {"Add", "Remove", "Update", "Find", "Where", "Select",
                            "First", "Single", "Any", "Count", "ToList", "SaveChanges",
                            "Include", "ThenInclude", "AsNoTracking", "Set", "Entry"}:
                tables.add(name)

        # Pattern 3: SQL in strings: FROM/JOIN/INTO/UPDATE/DELETE FROM table
        for m in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+\[?([A-Z]\w{2,})\]?", content, re.IGNORECASE):
            name = m.group(1)
            if name.lower() not in {"select", "where", "set", "values", "null", "table", "into"}:
                tables.add(name)

        for m in re.finditer(r"\bDELETE\s+FROM\s+\[?([A-Z]\w{2,})\]?", content, re.IGNORECASE):
            tables.add(m.group(1))

        # Pattern 4: [Table("Name")] attribute
        for m in re.finditer(r'\[Table\s*\(\s*"(\w+)"\s*\)\]', content):
            tables.add(m.group(1))

        # Pattern 5: Python __tablename__
        for m in re.finditer(r'__tablename__\s*=\s*["\'](\w+)["\']', content):
            tables.add(m.group(1))

        # Pattern 6: C# class that inherits from entity base (class Trip : BaseEntity)
        for m in re.finditer(r"class\s+(\w+)\s*:\s*(?:Base|Entity|Model|DbEntity)", content):
            tables.add(m.group(1))

        # Filter out noise
        filtered = set()
        for t in tables:
            if (
                t.lower() not in TABLE_NOISE
                and len(t) >= 3
                and not t.startswith("_")
                and t[0].isupper()  # Table/entity names start uppercase
            ):
                filtered.add(t)

        return filtered

    # ── Code Flow Extraction ──────────────────────────────────────────────

    def _extract_code_flows(self, content: str, filename: str) -> list[dict]:
        """Extract application layer patterns from code."""
        flows: list[dict] = []

        for layer_name, pattern in LAYER_PATTERNS.items():
            for match in pattern.finditer(content):
                class_name = match.group(1)
                if class_name.lower() not in TABLE_NOISE:
                    flows.append({
                        "layer": layer_name,
                        "class": class_name,
                        "file": filename,
                    })

        return flows

    # ── Reference Extraction ──────────────────────────────────────────────

    def _extract_references(
        self, content: str, entities: list[str], file_path: Path
    ) -> list[dict]:
        """Find entity references with context."""
        refs: list[dict] = []
        lines = content.split("\n")

        for entity in entities[:5]:
            if len(entity) < 3:
                continue
            pattern = re.compile(re.escape(entity), re.IGNORECASE)
            for i, line in enumerate(lines):
                if pattern.search(line):
                    refs.append({
                        "entity": entity,
                        "file": file_path.name,
                        "line": i + 1,
                        "context": line.strip()[:120],
                    })
                    if len(refs) >= 30:
                        return refs

        return refs

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_entities(title: str, description: str) -> list[str]:
        from task_analyzer.investigation.planner import EntityExtractor
        return EntityExtractor().extract(title, description)

    def is_available(self, connectors: dict) -> bool:
        return True  # Always available if repos exist
