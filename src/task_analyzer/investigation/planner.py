"""
Investigation Planner -- Domain-agnostic investigation planning.

Uses dynamic discovery instead of hardcoded mappings:

  1. EntityExtractor: extracts meaningful entities from task text using NLP
  2. SchemaDiscovery: discovers database tables and fuzzy-matches entities
  3. InvestigationPlanner: builds a plan from discovered entities and schema

No hardcoded table names, entity lists, or domain-specific keywords.
The system works with any repository, any database, any architecture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SYSTEM_MAP_PATH = Path.home() / ".traceai" / "system_map.json"


# ── Entity Extractor ──────────────────────────────────────────────────────────

# Common English stop words that are never entities
STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "and", "but", "or", "nor", "not", "no", "so",
    "if", "then", "than", "too", "very", "just", "about", "above",
    "below", "between", "this", "that", "these", "those", "it", "its",
    "when", "where", "how", "what", "which", "who", "whom", "why",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "also", "new", "old",
    "get", "getting", "got", "make", "making", "made", "take", "taking",
    "come", "coming", "go", "going", "see", "look", "find", "give",
    "use", "using", "used", "work", "working", "need", "try", "keep",
    "still", "already", "yet", "now", "here", "there", "up", "down",
    "out", "off", "over", "under", "again", "further", "once",
    "able", "unable", "issue", "problem", "bug", "error", "fix",
    "please", "check", "update", "change", "add", "remove",
    # Generic words that match too many code classes
    "open", "close", "list", "item", "data", "info", "type", "name",
    "value", "result", "status", "state", "event", "action", "task",
    "follow", "prod", "test", "main", "base", "core", "common",
    "even", "expected", "currently", "still", "working", "show",
    "showing", "display", "page", "view", "form", "button", "click",
    "ability", "inability", "feature", "details", "summary",
    "maintenance", "deployment", "configuration", "setting",
})


class EntityExtractor:
    """
    Extracts meaningful entities from task text using NLP tokenization.

    No hardcoded entity lists. Uses linguistic patterns to identify
    nouns, compound terms, and technical identifiers.
    """

    def extract(self, title: str, description: str = "") -> list[str]:
        """
        Extract entities from task text.

        Returns a deduplicated list of meaningful terms sorted by relevance.
        """
        text = f"{title} {description}"
        entities: list[str] = []
        seen: set[str] = set()

        # 1. Extract PascalCase identifiers (e.g., LoadPlan, TripEvent)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text):
            word = match.group(1)
            if word.lower() not in seen:
                entities.append(word)
                seen.add(word.lower())
                # Also add the split parts (LoadPlan -> load, plan)
                parts = re.findall(r"[A-Z][a-z]+", word)
                for p in parts:
                    if p.lower() not in seen and p.lower() not in STOP_WORDS and len(p) > 2:
                        seen.add(p.lower())

        # 2. Extract snake_case identifiers (e.g., trip_event, load_plan)
        for match in re.finditer(r"\b([a-z]+_[a-z_]+)\b", text.lower()):
            word = match.group(1)
            if word not in seen:
                entities.append(word)
                seen.add(word)

        # 3. Extract meaningful single words (nouns, technical terms)
        words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9]*\b", text)
        for word in words:
            lower = word.lower()
            is_acronym = word.isupper() and len(word) >= 3  # ITM, OVC, API
            min_len = 3 if is_acronym else 4  # Acronyms can be 3 chars
            if (
                lower not in seen
                and lower not in STOP_WORDS
                and len(lower) >= min_len
                and not lower.isdigit()
                and not re.match(r"^[a-z]{1,2}\d+", lower)  # Skip ticket refs like cr84899
                and not re.match(r"^\d+[a-z]*$", lower)  # Skip numeric refs like 07344649
            ):
                entities.append(word if is_acronym else lower)
                seen.add(lower)

        # 4. Extract quoted strings
        for match in re.finditer(r"['\"]([^'\"]+)['\"]", text):
            phrase = match.group(1).strip()
            if phrase.lower() not in seen and len(phrase) > 2:
                entities.append(phrase)
                seen.add(phrase.lower())

        return entities[:30]  # Limit to 30 entities


# ── Schema Discovery ──────────────────────────────────────────────────────────

class SchemaDiscovery:
    """
    Dynamically discovers database tables and matches them to entities.

    No hardcoded table names. Queries INFORMATION_SCHEMA at runtime
    and uses fuzzy matching to find relevant tables.
    """

    def __init__(self) -> None:
        self._cached_tables: list[str] | None = None

    def discover_tables(self, db_connector, tenant_db: str | None = None) -> list[str]:
        """Query the database for all table names with schema prefixes.

        Security: Schema queries are validated through SecurityGuard with
        allow_schema_inspection=True.
        """
        if self._cached_tables is not None:
            return self._cached_tables

        try:
            engine = db_connector._get_engine()
            from sqlalchemy import text
            from task_analyzer.core.security_guard import SecurityGuard

            guard = SecurityGuard(safe_mode=True)

            if tenant_db:
                schema_query = (
                    f"SELECT TABLE_SCHEMA, TABLE_NAME FROM {tenant_db}.INFORMATION_SCHEMA.TABLES "
                    f"WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
                )
                validated_query = guard.validate_sql_query(
                    schema_query, allow_schema_inspection=True
                )

                with engine.connect() as conn:
                    conn.execute(text("SET ROWCOUNT 500"))
                    result = conn.execute(text(validated_query))
                    # Store as schema.table for fully qualified access
                    self._cached_tables = [f"{row[0]}.{row[1]}" for row in result]
            else:
                from sqlalchemy import inspect
                inspector = inspect(engine)
                self._cached_tables = inspector.get_table_names()[:500]

            logger.info("schema_discovered", tables=len(self._cached_tables), db=tenant_db or "default")
            return self._cached_tables

        except Exception as exc:
            logger.warning("schema_discovery_failed", error=str(exc))
            return []

    def match_entities_to_tables(
        self, entities: list[str], tables: list[str]
    ) -> list[str]:
        """
        Fuzzy-match extracted entities to discovered table names.

        Tables may be schema-qualified (e.g., "Operation.Trip").
        Matching is done against the table name part only.
        """
        matched: list[str] = []
        matched_set: set[str] = set()

        for entity in entities:
            entity_lower = entity.lower().replace("_", "")

            if len(entity_lower) < 3:
                continue

            for full_table in tables:
                if full_table in matched_set:
                    continue

                # Extract just the table name (after the last dot)
                table_name = full_table.split(".")[-1].lower().replace("_", "")

                # Exact match
                if entity_lower == table_name:
                    matched.append(full_table)
                    matched_set.add(full_table)
                    continue

                # Entity is substring of table name
                if entity_lower in table_name:
                    matched.append(full_table)
                    matched_set.add(full_table)
                    continue

                # Plural/singular
                if (
                    entity_lower + "s" == table_name
                    or entity_lower == table_name + "s"
                    or entity_lower + "es" == table_name
                ):
                    matched.append(full_table)
                    matched_set.add(full_table)
                    continue

        return matched[:15]


# ── System Map (infrastructure config only, no domain knowledge) ──────────────

class SystemMap:
    """
    System architecture configuration.

    Contains ONLY infrastructure mappings:
      - services: name -> {repo, type}
      - flows: name -> [systems]
      - tenant_db_map: tenant -> database name

    Does NOT contain keyword-to-table mappings (those are discovered dynamically).
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.services: dict[str, dict] = data.get("services", {})
        self.databases: dict[str, dict] = data.get("databases", {})
        self.flows: dict[str, list[str]] = data.get("flows", {})
        self.tenant_db_map: dict[str, str] = data.get("tenant_db_map", {})

    def get_service_repo(self, service_name: str) -> str | None:
        svc = self.services.get(service_name, {})
        return svc.get("repo")

    def get_all_repos(self) -> list[str]:
        repos = set()
        for svc in self.services.values():
            if svc.get("repo"):
                repos.add(svc["repo"])
        return list(repos)

    def resolve_tenant_db(self, tenant_name: str) -> str | None:
        return self.tenant_db_map.get(tenant_name.lower())

    def get_all_tenant_names(self) -> list[str]:
        return list(self.tenant_db_map.keys())

    def summarize(self) -> str:
        parts = ["## System Architecture"]
        if self.services:
            parts.append("\n### Services")
            for name, info in self.services.items():
                repo = info.get("repo", info.get("type", "?"))
                parts.append(f"- **{name}** ({repo})")
        if self.flows:
            parts.append("\n### Flows")
            for name, systems in self.flows.items():
                parts.append(f"- **{name}**: {' -> '.join(systems)}")
        return "\n".join(parts)


def load_system_map() -> SystemMap:
    """Load system map from disk."""
    if not SYSTEM_MAP_PATH.exists():
        return SystemMap()
    try:
        data = json.loads(SYSTEM_MAP_PATH.read_text(encoding="utf-8"))
        logger.info("system_map_loaded", services=len(data.get("services", {})))
        return SystemMap(data)
    except Exception as exc:
        logger.warning("system_map_load_failed", error=str(exc))
        return SystemMap()


# ── Investigation Plan ────────────────────────────────────────────────────────

class InvestigationPlan:
    """Output of the planner."""

    def __init__(self) -> None:
        self.entities: list[str] = []
        self.systems: list[str] = []
        self.repos: list[str] = []
        self.skills: list[str] = []
        self.tables: list[str] = []          # Schema-discovered tables (fallback)
        self.table_ranks: dict[str, int] = {}  # table → rank (1=code, 2=index, 3=fk, 4=fuzzy)
        self.code_tables: list[str] = []     # Code-discovered tables (preferred)
        self.queries: list[str] = []
        self.matched_flows: list[str] = []
        self.tenant: str | None = None
        self.tenant_db: str | None = None

    def get_effective_tables(self) -> list[str]:
        """Return code-discovered tables if available, else schema-discovered."""
        return self.code_tables if self.code_tables else self.tables

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "systems": self.systems,
            "repos": self.repos,
            "skills": self.skills,
            "tables": self.tables,
            "code_tables": self.code_tables,
            "queries": self.queries,
            "matched_flows": self.matched_flows,
            "tenant": self.tenant,
            "tenant_db": self.tenant_db,
        }

    def summarize(self) -> str:
        parts = ["## Investigation Plan"]
        if self.entities:
            parts.append(f"Entities: {', '.join(self.entities[:10])}")
        if self.tenant:
            parts.append(f"Tenant: {self.tenant} (database: {self.tenant_db})")
        if self.systems:
            parts.append(f"Systems: {', '.join(self.systems)}")
        if self.repos:
            parts.append(f"Repositories: {', '.join(self.repos)}")
        if self.code_tables:
            parts.append(f"Code-Discovered Tables: {', '.join(self.code_tables)}")
        elif self.tables:
            parts.append(f"Schema-Discovered Tables: {', '.join(self.tables)}")
        if self.queries:
            parts.append(f"Planned Queries: {len(self.queries)}")
        return "\n".join(parts)

    def build_queries_from_code_tables(self, schema_tables: list[str]) -> None:
        """
        Build SQL queries for code-discovered tables by matching them
        against schema-qualified table names from the database.

        code_tables has simple names like 'Trip', 'ActualTrips'.
        schema_tables has qualified names like 'Operation.Trip', 'Reporting.ActualTrips'.
        """
        self.queries = []
        schema_map = {}
        for st in schema_tables:
            simple = st.split(".")[-1].lower()
            if simple not in schema_map:
                schema_map[simple] = st

        for code_table in self.code_tables[:5]:
            # Find the schema-qualified version
            qualified = schema_map.get(code_table.lower())
            if qualified:
                parts = qualified.split(".")
                bracketed = ".".join(f"[{p}]" for p in parts)
                if self.tenant_db:
                    self.queries.append(f"SELECT TOP 20 * FROM [{self.tenant_db}].{bracketed} ORDER BY 1 DESC")
                else:
                    self.queries.append(f"SELECT TOP 20 * FROM {bracketed} ORDER BY 1 DESC")
            else:
                # No schema match -- try direct query
                if self.tenant_db:
                    self.queries.append(f"SELECT TOP 20 * FROM [{self.tenant_db}].[dbo].[{code_table}] ORDER BY 1 DESC")
                else:
                    self.queries.append(f"SELECT TOP 20 * FROM [{code_table}] ORDER BY 1 DESC")


# ── Planner ───────────────────────────────────────────────────────────────────

class InvestigationPlanner:
    """
    Domain-agnostic investigation planner.

    Uses dynamic discovery:
      1. EntityExtractor identifies entities from task text
      2. RankedTableSelector picks relevant tables (replaces fuzzy matching)
      3. SystemMap provides infrastructure context (services, flows, tenants)

    No hardcoded keyword-to-table mappings.
    """

    def __init__(self, system_map: SystemMap | None = None,
                 workspace_index=None) -> None:
        self.system_map = system_map or load_system_map()
        self.entity_extractor = EntityExtractor()
        self.schema_discovery = SchemaDiscovery()
        self.workspace_index = workspace_index

    def plan(
        self,
        task_title: str,
        task_description: str = "",
        db_connector=None,
        classification=None,
    ) -> InvestigationPlan:
        """
        Analyze the task and produce an investigation plan.

        Args:
            task_title: The task title
            task_description: The task description
            db_connector: Optional SQL connector for schema discovery
            classification: Optional TaskClassification for skill selection
        """
        plan = InvestigationPlan()
        text = f"{task_title} {task_description}".lower()

        # Step 1: Extract entities (domain-agnostic)
        plan.entities = self.entity_extractor.extract(task_title, task_description)
        logger.info("entities_extracted", count=len(plan.entities), entities=plan.entities[:10])

        # Step 2: Detect tenant from text
        for tenant_name in self.system_map.get_all_tenant_names():
            if tenant_name in text or tenant_name.upper() in task_title.upper():
                plan.tenant = tenant_name
                plan.tenant_db = self.system_map.resolve_tenant_db(tenant_name)
                break

        # Step 3: Discover database schema and select tables using ranked selector
        if db_connector:
            all_tables = self.schema_discovery.discover_tables(db_connector, plan.tenant_db)

            # Get code-discovered tables from workspace index
            index_code_tables = []
            if self.workspace_index:
                index_code_tables = self.workspace_index.get_all_code_tables()

            # Use ranked selection instead of fuzzy matching
            try:
                from task_analyzer.workspace_intelligence.ranked_table_selector import RankedTableSelector
                selector = RankedTableSelector(
                    workspace_index=self.workspace_index,
                    max_tables=12,
                )
                ranked = selector.select(
                    entities=plan.entities,
                    all_schema_tables=all_tables,
                    code_tables=index_code_tables or None,
                )
                plan.tables = [r.qualified_name for r in ranked]
                plan.table_ranks = {r.qualified_name: r.rank for r in ranked}
            except Exception as exc:
                logger.debug("ranked_selection_fallback", error=str(exc))
                # Fallback to old fuzzy matching
                plan.tables = self.schema_discovery.match_entities_to_tables(plan.entities, all_tables)

            # Build queries for selected tables
            for table in plan.tables[:8]:
                parts = table.split(".")
                bracketed = ".".join(f"[{p}]" for p in parts)
                if plan.tenant_db:
                    plan.queries.append(f"SELECT TOP 20 * FROM [{plan.tenant_db}].{bracketed} ORDER BY 1 DESC")
                else:
                    plan.queries.append(f"SELECT TOP 20 * FROM {bracketed} ORDER BY 1 DESC")

        # Step 4: Match against flows (infrastructure, not domain)
        words = set(re.findall(r"[a-z]+", text))
        for flow_name, systems in self.system_map.flows.items():
            flow_words = set(re.findall(r"[a-z]+", flow_name.lower()))
            if flow_words & words:
                plan.matched_flows.append(flow_name)
                for system in systems:
                    if system not in plan.systems:
                        plan.systems.append(system)

        # Step 5: Match against service names
        for svc_name in self.system_map.services:
            if svc_name.lower() in text:
                if svc_name not in plan.systems:
                    plan.systems.append(svc_name)

        # Step 6: Determine repos from systems
        for system in plan.systems:
            repo = self.system_map.get_service_repo(system)
            if repo and repo not in plan.repos:
                plan.repos.append(repo)

        # Step 7: Determine skills (dynamic based on classification)
        if classification:
            plan.skills = list(classification.suggested_skills)
        else:
            plan.skills.append("RepoAnalysisSkill")
            plan.skills.append("TicketContextSkill")
            if plan.tables:
                plan.skills.append("DatabaseSchemaSkill")
                plan.skills.append("SQLQuerySkill")
            if plan.repos and len(plan.repos) > 1:
                plan.skills.append("CrossRepoAnalysisSkill")

        logger.info(
            "investigation_planned",
            entities=plan.entities[:5],
            tenant=plan.tenant,
            tables=plan.tables[:5],
            table_count=len(plan.tables),
            systems=plan.systems,
            repos=plan.repos,
        )

        return plan
