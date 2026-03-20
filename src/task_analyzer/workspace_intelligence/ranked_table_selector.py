"""
Ranked Table Selector — Replaces fuzzy matching with priority-ranked selection.

Ranking priority:
  1. Code-discovered tables (from workspace index class_table_refs)
  2. Index-mapped tables (classes matching entities reference these tables)
  3. Schema neighbor tables (connected via foreign keys)
  4. Fallback fuzzy matches (substring matching, lowest priority)

Only top-ranked tables are queried, eliminating false positives from
broad fuzzy matching.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RankedTable:
    """A table with its relevance rank and source."""

    def __init__(self, qualified_name: str, rank: int, source: str,
                 reason: str = "") -> None:
        self.qualified_name = qualified_name  # e.g., "Operation.Trip"
        self.table_name = qualified_name.split(".")[-1]
        self.rank = rank                      # 1=highest, 4=lowest
        self.source = source                  # code, index, fk_neighbor, fuzzy
        self.reason = reason

    def __repr__(self) -> str:
        return f"RankedTable({self.qualified_name}, rank={self.rank}, source={self.source})"


class RankedTableSelector:
    """
    Selects the most relevant database tables for an investigation.

    Uses a 4-tier ranking system instead of fuzzy matching:
      Rank 1: Tables directly referenced in code (DbSet, context.Table, FROM/JOIN)
      Rank 2: Tables referenced by classes that match task entities
      Rank 3: Tables connected via foreign keys to rank 1-2 tables
      Rank 4: Fuzzy substring matches (fallback only)

    Returns at most max_tables results, preferring higher-ranked tables.
    """

    def __init__(self, workspace_index=None, max_tables: int = 12) -> None:
        self.index = workspace_index
        self.max_tables = max_tables

    def select(
        self,
        entities: list[str],
        all_schema_tables: list[str],
        code_tables: list[str] | None = None,
        repo_names: list[str] | None = None,
    ) -> list[RankedTable]:
        """
        Select and rank tables for investigation.

        Args:
            entities: Extracted entities from task text
            all_schema_tables: All tables from INFORMATION_SCHEMA
            code_tables: Tables discovered from code analysis (if available)
            repo_names: Optional repo filter to scope index queries

        Returns:
            Ranked list of tables, highest priority first.
        """
        ranked: dict[str, RankedTable] = {}
        schema_lookup = self._build_schema_lookup(all_schema_tables)

        # ── Rank 1: Code-discovered tables ───────────────────────────────
        if code_tables:
            for tbl in code_tables:
                qualified = self._resolve_qualified(tbl, schema_lookup)
                if qualified and qualified not in ranked:
                    ranked[qualified] = RankedTable(
                        qualified, rank=1, source="code",
                        reason=f"Referenced in code as {tbl}",
                    )

        # ── Rank 2: Index-mapped tables (entity → class → table) ────────
        if self.index:
            for entity in entities[:10]:
                if len(entity) < 3:
                    continue

                # Find classes matching this entity
                classes = self.index.find_classes_by_entity(
                    entity, repo_names=repo_names,
                )
                for cls in classes[:5]:
                    # Find tables referenced by this class
                    tables = self.index.find_tables_referenced_by_class(cls["name"])
                    for tbl in tables:
                        qualified = self._resolve_qualified(tbl, schema_lookup)
                        if qualified and qualified not in ranked:
                            ranked[qualified] = RankedTable(
                                qualified, rank=2, source="index",
                                reason=f"Referenced by {cls['name']} ({cls['layer']})",
                            )

        # ── Rank 3: FK neighbor tables ───────────────────────────────────
        if self.index:
            rank_1_2_tables = [r.table_name for r in ranked.values() if r.rank <= 2]
            for tbl_name in rank_1_2_tables[:8]:
                neighbors = self.index.get_fk_neighbors(tbl_name)
                for neighbor in neighbors[:5]:
                    qualified = neighbor.get("qualified_name", "")
                    if qualified and qualified not in ranked:
                        ranked[qualified] = RankedTable(
                            qualified, rank=3, source="fk_neighbor",
                            reason=f"FK neighbor of {tbl_name}",
                        )

        # ── Rank 4: Fuzzy fallback (only if ranks 1-3 found < 3 tables) ─
        if len([r for r in ranked.values() if r.rank <= 3]) < 3:
            for entity in entities[:8]:
                if len(entity) < 4:
                    continue
                entity_lower = entity.lower().replace("_", "")
                for full_table in all_schema_tables:
                    if full_table in ranked:
                        continue
                    table_part = full_table.split(".")[-1].lower().replace("_", "")
                    # Exact match
                    if entity_lower == table_part:
                        ranked[full_table] = RankedTable(
                            full_table, rank=4, source="fuzzy",
                            reason=f"Exact match for entity '{entity}'",
                        )
                    # Entity is a significant substring (>= 5 chars to avoid noise)
                    elif len(entity_lower) >= 5 and entity_lower in table_part:
                        ranked[full_table] = RankedTable(
                            full_table, rank=4, source="fuzzy",
                            reason=f"Substring match for entity '{entity}'",
                        )

                    if len(ranked) >= self.max_tables * 2:
                        break

        # Sort by rank (ascending = highest priority first), then by name
        result = sorted(ranked.values(), key=lambda r: (r.rank, r.qualified_name))

        # Limit to max_tables
        result = result[:self.max_tables]

        logger.info(
            "tables_ranked",
            total=len(result),
            rank_1=len([r for r in result if r.rank == 1]),
            rank_2=len([r for r in result if r.rank == 2]),
            rank_3=len([r for r in result if r.rank == 3]),
            rank_4=len([r for r in result if r.rank == 4]),
        )

        return result

    def _build_schema_lookup(self, all_tables: list[str]) -> dict[str, str]:
        """Build a lookup from simple table name → qualified name."""
        lookup: dict[str, str] = {}
        for full in all_tables:
            simple = full.split(".")[-1].lower()
            if simple not in lookup:
                lookup[simple] = full
        return lookup

    def _resolve_qualified(self, table_name: str, schema_lookup: dict[str, str]) -> str | None:
        """Resolve a simple table name to its schema-qualified form."""
        # Already qualified
        if "." in table_name:
            return table_name
        return schema_lookup.get(table_name.lower())
