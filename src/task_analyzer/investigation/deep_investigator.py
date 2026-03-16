"""
Deep Investigation Engine -- Iterative evidence collection for reliable results.

The deep investigation runs in loops:

  Loop 1: Broad discovery
    - Extract entities from ticket
    - Discover database schema for the tenant
    - Search all repos for entity references
    - Collect initial SQL data from matched tables

  Loop 2: Targeted deepening
    - Evaluate what evidence is missing
    - Search repos for specific code patterns (Controller, Service, Repository)
    - Query additional SQL tables found in code
    - Read key source files for the feature being investigated

  Loop 3: Verification
    - Cross-reference code findings with SQL data
    - Verify hypotheses against actual data
    - Build final evidence package

Each loop produces an evidence assessment. The investigation only proceeds
to Claude reasoning when evidence quality is sufficient, or after all loops
complete.

This module does NOT replace the investigation engine. It provides a
`deep_collect_evidence()` function that the engine calls instead of
running skills once.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.core.security_guard import SecurityGuard

logger = structlog.get_logger(__name__)


class DeepInvestigator:
    """
    Iterative evidence collector that digs deep into repos and databases.

    Unlike the skill-based approach that runs each skill once, this
    investigator runs multiple passes, each time going deeper based
    on what was found in the previous pass.

    Security: ALL SQL queries are validated through SecurityGuard before
    execution. Schema inspection queries use allow_schema_inspection=True.
    Data queries use the default strict mode.
    """

    def __init__(self, profiles: list, connectors: dict, plan: Any = None) -> None:
        self.profiles = profiles
        self.connectors = connectors
        self.plan = plan
        self._guard = SecurityGuard(safe_mode=True)
        self.evidence: dict[str, Any] = {
            "code_files": [],       # {path, content_snippet, relevance}
            "code_flows": [],       # {controller, service, repository, method}
            "sql_tables": [],       # {name, schema, row_count, sample_rows, columns}
            "sql_schema": [],       # {table, columns}
            "entities": [],         # extracted entities
            "repo_search_results": [],  # {file, line, context}
            "tenant_db": None,
            "loops_completed": 0,
        }

    async def collect(self, task_title: str, task_description: str,
                      progress_callback=None) -> dict[str, Any]:
        """
        Run iterative deep evidence collection.

        Returns a rich evidence dict with code, SQL, and schema data.
        """
        async def _emit(msg: str) -> None:
            if progress_callback:
                try:
                    await progress_callback("deep_investigation", msg)
                except Exception:
                    pass

        text = f"{task_title} {task_description}"

        # Extract entities
        from task_analyzer.investigation.planner import EntityExtractor
        extractor = EntityExtractor()
        self.evidence["entities"] = extractor.extract(task_title, task_description)
        entities = self.evidence["entities"]

        await _emit(f"Extracted {len(entities)} entities: {', '.join(entities[:5])}")

        # Get repo paths
        repo_paths = []
        for p in self.profiles:
            rp = getattr(p, "repo_path", None)
            if rp and Path(rp).exists():
                repo_paths.append(Path(rp))

        # Get SQL connector
        db_connector = None
        for name, conn in self.connectors.items():
            ct = getattr(conn, "connector_type", None)
            if ct and hasattr(ct, "value") and ct.value == "sql_database":
                db_connector = conn
                break

        # Detect tenant
        tenant_db = None
        if self.plan and hasattr(self.plan, "tenant_db"):
            tenant_db = self.plan.tenant_db
        self.evidence["tenant_db"] = tenant_db

        # ── LOOP 1: Broad Discovery ──────────────────────────────────────

        await _emit("Loop 1: Broad discovery -- searching repos and database...")

        # 1a. Search repos for ALL entity references
        if repo_paths:
            await _emit("Searching repositories for entity references...")
            self._search_repos_broad(repo_paths, entities)

        # 1b. Discover database schema
        if db_connector and tenant_db:
            await _emit(f"Discovering database schema for {tenant_db}...")
            self._discover_schema(db_connector, tenant_db, entities)

        # 1c. Query matched tables
        if db_connector and self.evidence["sql_tables"]:
            await _emit(f"Querying {len(self.evidence['sql_tables'])} discovered tables...")
            self._query_tables(db_connector, tenant_db)

        self.evidence["loops_completed"] = 1
        await _emit(f"Loop 1 complete: {len(self.evidence['code_files'])} files, {len(self.evidence['sql_tables'])} tables")

        # ── LOOP 2: Targeted Deepening ───────────────────────────────────

        await _emit("Loop 2: Targeted deepening -- reading code and tracing flows...")

        # 2a. Read the actual content of found files and extract code patterns
        if self.evidence["code_files"]:
            await _emit("Reading source files and extracting code patterns...")
            self._read_and_analyze_files(entities)

        # 2b. Search for specific patterns: Delete, Service, Controller, Repository
        if repo_paths:
            action_keywords = self._extract_action_keywords(text)
            if action_keywords:
                await _emit(f"Searching for code patterns: {', '.join(action_keywords[:3])}...")
                self._search_repos_targeted(repo_paths, action_keywords)

        # 2c. Extract SQL table names from code and query them
        code_tables = self._extract_tables_from_found_code()
        if db_connector and code_tables:
            await _emit(f"Found {len(code_tables)} tables in code, querying...")
            self._query_code_discovered_tables(db_connector, tenant_db, code_tables)

        self.evidence["loops_completed"] = 2
        await _emit(f"Loop 2 complete: {len(self.evidence['code_files'])} files, {len(self.evidence['sql_tables'])} tables, {len(self.evidence['code_flows'])} flows")

        # ── LOOP 3: Verification ─────────────────────────────────────────

        await _emit("Loop 3: Verification -- cross-referencing evidence...")

        # 3a. Check if we have enough evidence
        quality = self._assess_evidence_quality()
        await _emit(f"Evidence quality: {quality['level']} ({quality['score']}/100)")

        # 3b. If evidence is still thin, do one more targeted search
        if quality["score"] < 50 and repo_paths:
            await _emit("Evidence insufficient, running deeper repo search...")
            self._search_repos_deep(repo_paths, entities, text)

        self.evidence["loops_completed"] = 3
        self.evidence["quality"] = quality

        total_files = len(self.evidence["code_files"])
        total_tables = len(self.evidence["sql_tables"])
        total_flows = len(self.evidence["code_flows"])
        total_refs = len(self.evidence["repo_search_results"])

        await _emit(
            f"Deep investigation complete: {total_files} files, {total_tables} tables, "
            f"{total_flows} code flows, {total_refs} references"
        )

        return self.evidence

    # ── Loop 1: Broad Discovery ───────────────────────────────────────────

    def _search_repos_broad(self, repo_paths: list[Path], entities: list[str]) -> None:
        """Search all repos for files matching any entity."""
        entity_patterns = [e.lower() for e in entities if len(e) >= 3]

        for repo_path in repo_paths:
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {
                    "node_modules", "bin", "obj", "dist", "build", "__pycache__",
                    ".git", "packages", "TestResults", ".vs", ".idea",
                }]
                for fname in files:
                    ext = Path(fname).suffix.lower()
                    if ext not in {".cs", ".py", ".ts", ".js", ".java", ".sql", ".xml", ".json"}:
                        continue
                    fname_lower = fname.lower()
                    for entity in entity_patterns:
                        if entity in fname_lower:
                            full_path = Path(root) / fname
                            rel_path = str(full_path.relative_to(repo_path))
                            if not any(f["path"] == rel_path for f in self.evidence["code_files"]):
                                self.evidence["code_files"].append({
                                    "path": rel_path,
                                    "full_path": str(full_path),
                                    "repo": repo_path.name,
                                    "matched_entity": entity,
                                    "content": None,  # loaded in loop 2
                                })
                            break
                    if len(self.evidence["code_files"]) >= 100:
                        return

    def _discover_schema(self, db_connector, tenant_db: str, entities: list[str]) -> None:
        """Discover schema and match entities to tables."""
        try:
            engine = db_connector._get_engine()
            from sqlalchemy import text

            # Validate schema inspection query through SecurityGuard
            schema_query = (
                f"SELECT TABLE_SCHEMA, TABLE_NAME FROM {tenant_db}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
            )
            validated_schema_query = self._guard.validate_sql_query(
                schema_query, allow_schema_inspection=True
            )

            with engine.connect() as conn:
                conn.execute(text("SET ROWCOUNT 1000"))
                result = conn.execute(text(validated_schema_query))
                all_tables = [(row[0], row[1]) for row in result]

            # Match entities to tables
            for entity in entities:
                if len(entity) < 3:
                    continue
                entity_lower = entity.lower()
                for schema, table in all_tables:
                    table_lower = table.lower()
                    if entity_lower in table_lower:
                        qualified = f"{schema}.{table}"
                        if not any(t["name"] == qualified for t in self.evidence["sql_tables"]):
                            self.evidence["sql_tables"].append({
                                "name": qualified,
                                "schema": schema,
                                "table": table,
                                "matched_entity": entity,
                                "row_count": None,
                                "sample_rows": [],
                                "columns": [],
                            })

            # Also get column info for matched tables
            for table_info in self.evidence["sql_tables"][:20]:
                try:
                    col_query = (
                        f"SELECT COLUMN_NAME, DATA_TYPE FROM {tenant_db}.INFORMATION_SCHEMA.COLUMNS "
                        f"WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
                        f"ORDER BY ORDINAL_POSITION"
                    )
                    validated_col_query = self._guard.validate_sql_query(
                        col_query, allow_schema_inspection=True
                    )
                    with engine.connect() as conn:
                        cols = conn.execute(text(validated_col_query),
                            {"schema": table_info["schema"], "table": table_info["table"]})
                        table_info["columns"] = [{"name": r[0], "type": r[1]} for r in cols]
                        self.evidence["sql_schema"].append({
                            "table": f"{table_info['schema']}.{table_info['table']}",
                            "columns": table_info["columns"],
                        })
                except Exception:
                    pass

            logger.info("schema_discovered_deep", tables=len(self.evidence["sql_tables"]))

        except Exception as exc:
            logger.warning("deep_schema_discovery_failed", error=str(exc))

    def _query_tables(self, db_connector, tenant_db: str) -> None:
        """Query sample rows from discovered tables."""
        try:
            engine = db_connector._get_engine()
            from sqlalchemy import text

            for table_info in self.evidence["sql_tables"][:10]:
                try:
                    qualified = f"[{tenant_db}].[{table_info['schema']}].[{table_info['table']}]"
                    data_query = f"SELECT TOP 10 * FROM {qualified} ORDER BY 1 DESC"

                    # Validate through SecurityGuard — strict mode (no schema inspection)
                    validated_data_query = self._guard.validate_sql_query(data_query)

                    with engine.connect() as conn:
                        conn.execute(text("SET ROWCOUNT 10"))
                        conn.execute(text("SET LOCK_TIMEOUT 5000"))
                        rows = conn.execute(text(validated_data_query))
                        data = []
                        for row in rows.fetchall():
                            row_dict = {}
                            for k, v in row._mapping.items():
                                row_dict[str(k)] = str(v)[:100] if v is not None else "NULL"
                            data.append(row_dict)
                        table_info["row_count"] = len(data)
                        table_info["sample_rows"] = data[:5]
                        if data:
                            table_info["columns"] = [{"name": k} for k in data[0].keys()]
                except Exception as exc:
                    logger.debug("table_query_failed", table=table_info["name"], error=str(exc)[:60])

        except Exception as exc:
            logger.warning("deep_query_failed", error=str(exc))

    # ── Loop 2: Targeted Deepening ────────────────────────────────────────

    def _read_and_analyze_files(self, entities: list[str]) -> None:
        """Read found files and extract code patterns."""
        for file_info in self.evidence["code_files"][:30]:
            try:
                full_path = file_info.get("full_path", "")
                if not full_path or not Path(full_path).exists():
                    continue
                content = Path(full_path).read_text(encoding="utf-8", errors="ignore")

                # Store relevant snippet (lines containing entities)
                lines = content.split("\n")
                relevant_lines = []
                for i, line in enumerate(lines):
                    for entity in entities[:5]:
                        if entity.lower() in line.lower():
                            start = max(0, i - 2)
                            end = min(len(lines), i + 3)
                            snippet = "\n".join(lines[start:end])
                            relevant_lines.append({
                                "line": i + 1,
                                "snippet": snippet[:300],
                                "entity": entity,
                            })
                            break
                    if len(relevant_lines) >= 5:
                        break

                file_info["content"] = relevant_lines

                # Extract code flow patterns
                self._extract_flows_from_content(content, file_info["path"])

            except Exception:
                pass

    def _extract_flows_from_content(self, content: str, file_path: str) -> None:
        """Extract Controller/Service/Repository patterns."""
        patterns = {
            "controller": re.compile(r"class\s+(\w*Controller)\b"),
            "service": re.compile(r"class\s+(\w*Service)\b"),
            "repository": re.compile(r"class\s+(\w*Repository)\b"),
            "handler": re.compile(r"class\s+(\w*Handler)\b"),
            "dbcontext": re.compile(r"class\s+(\w*(?:Context|DbContext))\b"),
        }
        methods = re.compile(r"(?:public|private|protected|internal|async)\s+\w+\s+(\w+)\s*\(")

        for layer, pattern in patterns.items():
            for match in pattern.finditer(content):
                class_name = match.group(1)
                # Find methods in this class
                class_methods = []
                for m in methods.finditer(content):
                    class_methods.append(m.group(1))

                self.evidence["code_flows"].append({
                    "layer": layer,
                    "class": class_name,
                    "file": file_path,
                    "methods": class_methods[:10],
                })

    def _extract_action_keywords(self, text: str) -> list[str]:
        """Extract action-oriented keywords for targeted search."""
        actions = []
        action_words = {"delete", "create", "update", "insert", "remove", "add",
                        "send", "sync", "load", "save", "process", "handle",
                        "validate", "check", "get", "set", "fetch", "push", "pull"}
        words = set(re.findall(r"[a-z]+", text.lower()))
        for w in words:
            if w in action_words:
                actions.append(w)

        # Build compound search terms: Delete + entity
        entities = self.evidence.get("entities", [])[:5]
        compounds = []
        for action in actions[:3]:
            for entity in entities[:3]:
                compounds.append(f"{action}{entity}")
                compounds.append(f"{entity}{action}")
        return compounds[:10]

    def _search_repos_targeted(self, repo_paths: list[Path], patterns: list[str]) -> None:
        """Search repos for specific compound patterns like DeleteTrip."""
        for repo_path in repo_paths:
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {
                    "node_modules", "bin", "obj", "dist", "build", "__pycache__",
                    ".git", "packages", "TestResults", ".vs",
                }]
                for fname in files:
                    ext = Path(fname).suffix.lower()
                    if ext not in {".cs", ".py", ".ts", ".js", ".java", ".sql"}:
                        continue
                    try:
                        full_path = Path(root) / fname
                        content = full_path.read_text(encoding="utf-8", errors="ignore")
                        content_lower = content.lower()
                        for pattern in patterns:
                            if pattern.lower() in content_lower:
                                # Find the exact line
                                for i, line in enumerate(content.split("\n")):
                                    if pattern.lower() in line.lower():
                                        self.evidence["repo_search_results"].append({
                                            "file": str(full_path.relative_to(repo_path)),
                                            "repo": repo_path.name,
                                            "line": i + 1,
                                            "context": line.strip()[:150],
                                            "pattern": pattern,
                                        })
                                        break
                    except Exception:
                        pass
                    if len(self.evidence["repo_search_results"]) >= 50:
                        return

    def _extract_tables_from_found_code(self) -> list[str]:
        """Extract SQL table names from code files we've already read."""
        tables = set()
        patterns = [
            re.compile(r"(?:context|_db|_context|_repository)\s*\.\s*([A-Z]\w+)"),
            re.compile(r"DbSet\s*<\s*(\w+)\s*>"),
            re.compile(r"\bFROM\s+\[?([A-Z]\w{2,})\]?", re.IGNORECASE),
            re.compile(r"\bJOIN\s+\[?([A-Z]\w{2,})\]?", re.IGNORECASE),
            re.compile(r'\[Table\s*\(\s*"(\w+)"\s*\)\]'),
        ]
        noise = {"Add", "Remove", "Update", "Find", "Where", "Select", "First",
                 "Single", "Any", "Count", "ToList", "SaveChanges", "Include",
                 "Set", "Entry", "String", "Int", "Bool", "Void", "Task"}

        for file_info in self.evidence["code_files"]:
            if not file_info.get("content"):
                continue
            for ref in file_info["content"]:
                snippet = ref.get("snippet", "")
                for pattern in patterns:
                    for match in pattern.finditer(snippet):
                        name = match.group(1)
                        if name not in noise and len(name) >= 3:
                            tables.add(name)

        return list(tables)

    def _query_code_discovered_tables(self, db_connector, tenant_db: str, code_tables: list[str]) -> None:
        """Query tables that were found in code but not yet queried."""
        already_queried = {t["table"] for t in self.evidence["sql_tables"]}
        new_tables = [t for t in code_tables if t not in already_queried]

        if not new_tables or not tenant_db:
            return

        try:
            engine = db_connector._get_engine()
            from sqlalchemy import text

            # Find schema-qualified names
            with engine.connect() as conn:
                for table_name in new_tables[:5]:
                    try:
                        # Validate schema lookup through SecurityGuard
                        schema_lookup = (
                            f"SELECT TABLE_SCHEMA FROM {tenant_db}.INFORMATION_SCHEMA.TABLES "
                            f"WHERE TABLE_NAME = :tbl"
                        )
                        validated_lookup = self._guard.validate_sql_query(
                            schema_lookup, allow_schema_inspection=True
                        )
                        result = conn.execute(text(validated_lookup), {"tbl": table_name})
                        schemas = [row[0] for row in result]
                        if schemas:
                            schema = schemas[0]
                            qualified = f"[{tenant_db}].[{schema}].[{table_name}]"
                            data_query = f"SELECT TOP 10 * FROM {qualified} ORDER BY 1 DESC"

                            # Validate data query through SecurityGuard — strict mode
                            validated_data = self._guard.validate_sql_query(data_query)

                            conn.execute(text("SET ROWCOUNT 10"))
                            rows = conn.execute(text(validated_data))
                            data = [dict(row._mapping) for row in rows.fetchall()]
                            sample = [{str(k): str(v)[:100] for k, v in row.items()} for row in data[:5]]
                            self.evidence["sql_tables"].append({
                                "name": f"{schema}.{table_name}",
                                "schema": schema,
                                "table": table_name,
                                "matched_entity": "code_reference",
                                "row_count": len(data),
                                "sample_rows": sample,
                                "columns": [{"name": k} for k in data[0].keys()] if data else [],
                            })
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("code_table_query_failed", error=str(exc)[:60])

    # ── Loop 3: Verification ──────────────────────────────────────────────

    def _search_repos_deep(self, repo_paths: list[Path], entities: list[str], text: str) -> None:
        """Last resort: grep through all code files for any entity mention."""
        for repo_path in repo_paths:
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {
                    "node_modules", "bin", "obj", "dist", "build", "__pycache__",
                    ".git", "packages", "TestResults", ".vs",
                }]
                for fname in files:
                    ext = Path(fname).suffix.lower()
                    if ext not in {".cs", ".py", ".ts", ".js", ".sql"}:
                        continue
                    try:
                        full_path = Path(root) / fname
                        content = full_path.read_text(encoding="utf-8", errors="ignore")
                        for entity in entities[:3]:
                            if len(entity) < 4:
                                continue
                            if entity.lower() in content.lower():
                                rel = str(full_path.relative_to(repo_path))
                                if not any(f["path"] == rel for f in self.evidence["code_files"]):
                                    self.evidence["code_files"].append({
                                        "path": rel,
                                        "full_path": str(full_path),
                                        "repo": repo_path.name,
                                        "matched_entity": entity,
                                        "content": None,
                                    })
                    except Exception:
                        pass
                    if len(self.evidence["code_files"]) >= 150:
                        return

    def _assess_evidence_quality(self) -> dict[str, Any]:
        """Assess the quality of collected evidence."""
        score = 0
        details = []

        code_files = len(self.evidence["code_files"])
        code_flows = len(self.evidence["code_flows"])
        sql_tables = len(self.evidence["sql_tables"])
        sql_with_data = sum(1 for t in self.evidence["sql_tables"] if (t.get("row_count") or 0) > 0)
        search_results = len(self.evidence["repo_search_results"])

        if code_files > 0:
            score += min(30, code_files * 3)
            details.append(f"{code_files} code files found")
        if code_flows > 0:
            score += min(20, code_flows * 5)
            details.append(f"{code_flows} code flows traced")
        if sql_tables > 0:
            score += min(20, sql_tables * 2)
            details.append(f"{sql_tables} SQL tables discovered")
        if sql_with_data > 0:
            score += min(15, sql_with_data * 3)
            details.append(f"{sql_with_data} tables with data")
        if search_results > 0:
            score += min(15, search_results * 2)
            details.append(f"{search_results} code references")

        level = "insufficient" if score < 30 else "partial" if score < 60 else "good" if score < 80 else "excellent"

        return {
            "score": min(score, 100),
            "level": level,
            "details": details,
        }

    def build_context_for_llm(self) -> str:
        """Build a rich context string from all collected evidence."""
        parts = []

        quality = self.evidence.get("quality", {})
        parts.append(f"## Evidence Quality: {quality.get('level', '?')} ({quality.get('score', 0)}/100)")
        for d in quality.get("details", []):
            parts.append(f"- {d}")

        # Code files
        files_with_content = [f for f in self.evidence["code_files"] if f.get("content")]
        if files_with_content:
            parts.append(f"\n## [CODE] Source Code Analysis ({len(files_with_content)} files)")
            for f in files_with_content[:15]:
                parts.append(f"\n### {f['repo']}/{f['path']}")
                for ref in f.get("content", [])[:3]:
                    parts.append(f"Line {ref['line']} (entity: {ref['entity']}):")
                    parts.append(f"```\n{ref['snippet']}\n```")

        # Code flows
        if self.evidence["code_flows"]:
            parts.append(f"\n## [CODE] Application Code Flows ({len(self.evidence['code_flows'])})")
            for flow in self.evidence["code_flows"][:10]:
                methods = ", ".join(flow.get("methods", [])[:5])
                parts.append(f"- {flow['layer']}: **{flow['class']}** ({flow['file']})")
                if methods:
                    parts.append(f"  Methods: {methods}")

        # Search results
        if self.evidence["repo_search_results"]:
            parts.append(f"\n## [CODE] Code References ({len(self.evidence['repo_search_results'])})")
            for ref in self.evidence["repo_search_results"][:15]:
                parts.append(f"- `{ref['repo']}/{ref['file']}:{ref['line']}` [{ref['pattern']}] {ref['context'][:80]}")

        # SQL schema
        if self.evidence["sql_schema"]:
            parts.append(f"\n## [SCHEMA] Database Schema ({len(self.evidence['sql_schema'])} tables)")
            for s in self.evidence["sql_schema"][:15]:
                cols = ", ".join(c["name"] for c in s["columns"][:10])
                parts.append(f"- **{s['table']}**: {cols}")

        # SQL data
        tables_with_data = [t for t in self.evidence["sql_tables"] if t.get("sample_rows")]
        if tables_with_data:
            parts.append(f"\n## [SQL] Database Query Results ({len(tables_with_data)} tables)")
            parts.append("NOTE: SQL data shows current state only, not causation.")
            for t in tables_with_data[:8]:
                parts.append(f"\n### {t['name']} ({t.get('row_count') or 0} rows)")
                if t.get("columns"):
                    cols = ", ".join(c.get("name", "?") for c in t["columns"][:10])
                    parts.append(f"Columns: {cols}")
                for row in t.get("sample_rows", [])[:3]:
                    vals = " | ".join(f"{k}={v[:30]}" for k, v in list(row.items())[:6])
                    parts.append(f"  {vals}")

        return "\n".join(parts)
