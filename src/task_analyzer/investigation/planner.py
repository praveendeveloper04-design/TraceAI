"""
Investigation Planner -- Determines which systems, repos, and skills
are needed for a given investigation.

Loads the system map from ~/.traceai/system_map.json and uses keyword
matching to identify involved systems, required repositories, and
SQL queries to run.

The planner runs BEFORE skills execute, so the investigation engine
knows which repos to load and which skills to activate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SYSTEM_MAP_PATH = Path.home() / ".traceai" / "system_map.json"


# ── System Map ────────────────────────────────────────────────────────────────

class SystemMap:
    """Loaded system architecture map."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.services: dict[str, dict] = data.get("services", {})
        self.databases: dict[str, dict] = data.get("databases", {})
        self.flows: dict[str, list[str]] = data.get("flows", {})
        self.keywords: dict[str, list[str]] = data.get("keywords", {})

    def get_service_repo(self, service_name: str) -> str | None:
        svc = self.services.get(service_name, {})
        return svc.get("repo")

    def get_all_repos(self) -> list[str]:
        repos = set()
        for svc in self.services.values():
            if svc.get("repo"):
                repos.add(svc["repo"])
        return list(repos)

    def get_tables_for_db(self, db_name: str) -> list[str]:
        return self.databases.get(db_name, {}).get("tables", [])

    def get_all_tables(self) -> list[str]:
        tables = []
        for db in self.databases.values():
            tables.extend(db.get("tables", []))
        return tables

    def get_flow_systems(self, flow_name: str) -> list[str]:
        return self.flows.get(flow_name, [])

    def summarize(self) -> str:
        parts = ["## System Architecture"]
        if self.services:
            parts.append("\n### Services")
            for name, info in self.services.items():
                repo = info.get("repo", info.get("type", "?"))
                parts.append(f"- **{name}** ({repo})")
        if self.databases:
            parts.append("\n### Databases")
            for name, info in self.databases.items():
                tables = info.get("tables", [])
                parts.append(f"- **{name}**: {', '.join(tables[:10])}")
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
        return data if isinstance(data, SystemMap) else SystemMap(data)
    except Exception as exc:
        logger.warning("system_map_load_failed", error=str(exc))
        return SystemMap()


# ── Investigation Plan ────────────────────────────────────────────────────────

class InvestigationPlan:
    """Output of the planner -- what the investigation should do."""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.repos: list[str] = []
        self.skills: list[str] = []
        self.tables: list[str] = []
        self.queries: list[str] = []
        self.matched_keywords: list[str] = []
        self.matched_flows: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "systems": self.systems,
            "repos": self.repos,
            "skills": self.skills,
            "tables": self.tables,
            "queries": self.queries,
            "matched_keywords": self.matched_keywords,
            "matched_flows": self.matched_flows,
        }

    def summarize(self) -> str:
        parts = ["## Investigation Plan"]
        if self.systems:
            parts.append(f"Systems: {', '.join(self.systems)}")
        if self.repos:
            parts.append(f"Repositories: {', '.join(self.repos)}")
        if self.tables:
            parts.append(f"SQL Tables: {', '.join(self.tables)}")
        if self.queries:
            parts.append(f"Planned Queries: {len(self.queries)}")
        if self.matched_flows:
            parts.append(f"Matched Flows: {', '.join(self.matched_flows)}")
        return "\n".join(parts)


# ── Planner ───────────────────────────────────────────────────────────────────

# Default keyword -> system/table mappings when system_map.json
# doesn't define custom keywords
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "trip": ["Trips", "TripEvents", "TripDetails"],
    "device": ["Devices", "DeviceConfig"],
    "tenant": ["Tenants", "TenantConfig"],
    "log": ["Logs", "EventLogs", "PLCLogs"],
    "delete": [],
    "load": ["LoadPlans", "LoadPlanDetails"],
    "delivery": ["Deliveries", "DeliveryEvents"],
    "order": ["Orders", "OrderDetails"],
    "driver": ["Drivers", "DriverSessions"],
    "vehicle": ["Vehicles", "VehicleConfig"],
    "alarm": ["Alarms", "AlarmEvents"],
    "hub": ["HubEvents", "HubConnections"],
    "ovc": [],
    "itm": [],
    "sync": ["SyncStatus", "SyncEvents"],
    "error": ["ErrorLogs"],
    "timeout": [],
    "null": [],
    "crash": [],
    "exception": [],
}


class InvestigationPlanner:
    """
    Analyzes a task and determines which systems, repos, and skills
    are needed for the investigation.

    Uses keyword matching against the system map to build a plan.
    """

    def __init__(self, system_map: SystemMap | None = None) -> None:
        self.system_map = system_map or load_system_map()

    def plan(self, task_title: str, task_description: str = "") -> InvestigationPlan:
        """
        Analyze the task and produce an investigation plan.

        Args:
            task_title: The task title
            task_description: The task description

        Returns:
            InvestigationPlan with systems, repos, skills, tables, queries
        """
        plan = InvestigationPlan()
        text = f"{task_title} {task_description}".lower()

        # Extract keywords from the text
        words = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower()))

        # Match against system map keywords
        keyword_map = self.system_map.keywords or DEFAULT_KEYWORDS
        for keyword, tables in keyword_map.items():
            if keyword in words or keyword in text:
                plan.matched_keywords.append(keyword)
                for table in tables:
                    if table not in plan.tables:
                        plan.tables.append(table)

        # Also check default keywords if system map doesn't have custom ones
        if not self.system_map.keywords:
            for keyword, tables in DEFAULT_KEYWORDS.items():
                if keyword in words or keyword in text:
                    if keyword not in plan.matched_keywords:
                        plan.matched_keywords.append(keyword)
                    for table in tables:
                        if table not in plan.tables:
                            plan.tables.append(table)

        # Match against flows
        for flow_name, systems in self.system_map.flows.items():
            flow_words = set(re.findall(r"[a-z]+", flow_name.lower()))
            if flow_words & words:
                plan.matched_flows.append(flow_name)
                for system in systems:
                    if system not in plan.systems:
                        plan.systems.append(system)

        # Match against service names
        for svc_name in self.system_map.services:
            if svc_name.lower() in text:
                if svc_name not in plan.systems:
                    plan.systems.append(svc_name)

        # Determine repos from systems
        for system in plan.systems:
            repo = self.system_map.get_service_repo(system)
            if repo and repo not in plan.repos:
                plan.repos.append(repo)

        # Add tables from system map databases
        for table in self.system_map.get_all_tables():
            table_lower = table.lower()
            if table_lower in text and table not in plan.tables:
                plan.tables.append(table)

        # Determine skills
        plan.skills.append("RepoAnalysisSkill")
        plan.skills.append("TicketContextSkill")

        if plan.tables:
            plan.skills.append("DatabaseSchemaSkill")
            plan.skills.append("SQLQuerySkill")
            # Build queries for matched tables
            for table in plan.tables[:5]:
                plan.queries.append(
                    f"SELECT TOP 20 * FROM [{table}] ORDER BY 1 DESC"
                )

        if plan.repos and len(plan.repos) > 1:
            plan.skills.append("CrossRepoAnalysisSkill")

        logger.info(
            "investigation_planned",
            keywords=plan.matched_keywords,
            systems=plan.systems,
            repos=plan.repos,
            tables=plan.tables,
            queries=len(plan.queries),
        )

        return plan
