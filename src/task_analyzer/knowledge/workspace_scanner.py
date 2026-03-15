"""
Workspace Scanner -- Multi-repository awareness for TraceAI.

Manages workspace profiles that define which repositories are related
and how they depend on each other. When investigating a task in one repo,
TraceAI automatically loads dependent repos for cross-repo analysis.

Workspace profile: ~/.traceai/workspace_profile.json

Structure:
  {
    "repos": [
      {"name": "Oildroid", "path": "C:/dev/Oildroid"},
      {"name": "PLC", "path": "C:/dev/PLC"}
    ],
    "dependencies": {
      "Oildroid": ["PLC"]
    },
    "services": {
      "PDIUnitManager": {"repo": "PLC", "path": "PDIUnitManager/"}
    }
  }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.models.schemas import ProjectProfile

logger = structlog.get_logger(__name__)

WORKSPACE_PROFILE_PATH = Path.home() / ".traceai" / "workspace_profile.json"


class WorkspaceProfile:
    """Loaded workspace profile with repos, dependencies, and services."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.repos: list[dict[str, str]] = data.get("repos", [])
        self.dependencies: dict[str, list[str]] = data.get("dependencies", {})
        self.services: dict[str, dict[str, str]] = data.get("services", {})

    def get_repo_path(self, name: str) -> str | None:
        for r in self.repos:
            if r.get("name") == name:
                return r.get("path")
        return None

    def get_dependencies(self, repo_name: str) -> list[str]:
        return self.dependencies.get(repo_name, [])

    def get_all_repo_names(self) -> list[str]:
        return [r["name"] for r in self.repos if "name" in r]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repos": self.repos,
            "dependencies": self.dependencies,
            "services": self.services,
        }


def load_workspace_profile() -> WorkspaceProfile:
    """Load workspace profile from disk, or return empty profile."""
    if not WORKSPACE_PROFILE_PATH.exists():
        return WorkspaceProfile()
    try:
        data = json.loads(WORKSPACE_PROFILE_PATH.read_text(encoding="utf-8"))
        logger.info("workspace_profile_loaded", repos=len(data.get("repos", [])))
        return WorkspaceProfile(data)
    except Exception as exc:
        logger.warning("workspace_profile_load_failed", error=str(exc))
        return WorkspaceProfile()


def save_workspace_profile(profile: WorkspaceProfile) -> None:
    """Save workspace profile to disk."""
    WORKSPACE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_PROFILE_PATH.write_text(
        json.dumps(profile.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    logger.info("workspace_profile_saved", repos=len(profile.repos))


class WorkspaceScanner:
    """
    Scans all repos in the workspace profile and builds a dependency graph.

    Detects:
      - Services in each repo (by directory structure)
      - Cross-repo dependencies
      - Shared database connections
    """

    def __init__(self, workspace: WorkspaceProfile) -> None:
        self.workspace = workspace

    def scan_all(self, store) -> list[ProjectProfile]:
        """Scan all repos and return their profiles."""
        from task_analyzer.knowledge.scanner import RepositoryScanner

        profiles: list[ProjectProfile] = []
        for repo in self.workspace.repos:
            name = repo.get("name", "")
            path = repo.get("path", "")
            if not path or not Path(path).exists():
                logger.warning("workspace_repo_not_found", name=name, path=path)
                continue

            # Check for cached profile first
            cached = store.load_profile(name)
            if cached:
                profiles.append(cached)
                logger.debug("workspace_profile_cached", repo=name)
                continue

            # Scan fresh
            try:
                scanner = RepositoryScanner(path)
                profile = scanner.scan()
                store.save_profile(profile)
                profiles.append(profile)
                logger.info("workspace_repo_scanned", repo=name)
            except Exception as exc:
                logger.warning("workspace_scan_failed", repo=name, error=str(exc))

        return profiles

    def get_dependency_profiles(
        self, repo_name: str, store
    ) -> list[ProjectProfile]:
        """Get profiles for all repos that the given repo depends on."""
        dep_names = self.workspace.get_dependencies(repo_name)
        profiles: list[ProjectProfile] = []
        for dep_name in dep_names:
            dep_path = self.workspace.get_repo_path(dep_name)
            if not dep_path:
                continue
            cached = store.load_profile(dep_name)
            if cached:
                profiles.append(cached)
            else:
                try:
                    from task_analyzer.knowledge.scanner import RepositoryScanner
                    scanner = RepositoryScanner(dep_path)
                    profile = scanner.scan()
                    store.save_profile(profile)
                    profiles.append(profile)
                except Exception as exc:
                    logger.warning("dependency_scan_failed", repo=dep_name, error=str(exc))
        return profiles

    def build_service_map(self) -> dict[str, dict[str, str]]:
        """Build a map of service name -> {repo, path}."""
        return dict(self.workspace.services)

    def summarize(self) -> str:
        """Produce a text summary for LLM context."""
        parts = ["## Workspace Architecture"]

        if self.workspace.repos:
            parts.append("\n### Repositories")
            for r in self.workspace.repos:
                parts.append(f"- **{r.get('name', '?')}**: `{r.get('path', '?')}`")

        if self.workspace.dependencies:
            parts.append("\n### Dependencies")
            for repo, deps in self.workspace.dependencies.items():
                for dep in deps:
                    parts.append(f"- {repo} -> depends_on -> {dep}")

        if self.workspace.services:
            parts.append("\n### Services")
            for svc, info in self.workspace.services.items():
                parts.append(f"- **{svc}** in {info.get('repo', '?')} (`{info.get('path', '?')}`)")

        return "\n".join(parts)
