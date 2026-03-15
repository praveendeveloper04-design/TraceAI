"""
CrossRepoAnalysisSkill -- Analyzes dependent repositories during investigation.

When investigating a task in Repo A, this skill automatically loads
profiles from repos that A depends on (defined in workspace_profile.json)
and searches them for relevant files and services.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class CrossRepoAnalysisSkill(BaseSkill):
    """Analyzes dependent repositories for cross-repo context."""

    name = "cross_repo_analysis"
    display_name = "Cross-Repository Analysis"
    description = "Loads dependent repository profiles and searches for related services"
    required_tools = ["RepoReader"]

    async def run(self, task, context, security_guard, connectors, graph) -> dict[str, Any]:
        result: dict[str, Any] = {
            "dependent_repos": [],
            "related_services": [],
            "cross_repo_files": [],
        }

        start = time.time()

        try:
            security_guard.validate_tool("RepoReader", "list_files")

            # Load workspace profile
            from task_analyzer.knowledge.workspace_scanner import (
                WorkspaceScanner, load_workspace_profile,
            )
            from task_analyzer.storage.local_store import LocalStore

            workspace = load_workspace_profile()
            if not workspace.repos:
                logger.debug("cross_repo_skipped", reason="no workspace profile")
                return result

            store = LocalStore()
            scanner = WorkspaceScanner(workspace)

            # Find which repo this task belongs to
            profiles = context.get("profiles", [])
            current_repo = None
            if profiles:
                current_repo = profiles[0].repo_name if hasattr(profiles[0], "repo_name") else None

            if not current_repo:
                return result

            # Load dependent repo profiles
            dep_profiles = scanner.get_dependency_profiles(current_repo, store)
            for dp in dep_profiles:
                result["dependent_repos"].append({
                    "name": dp.repo_name,
                    "language": dp.primary_language,
                    "services": len(dp.services),
                })

                # Add to graph
                graph.add_node(f"repo:{dp.repo_name}", "repository", {
                    "label": dp.repo_name,
                    "language": dp.primary_language,
                })
                if current_repo:
                    graph.add_edge(f"repo:{current_repo}", f"repo:{dp.repo_name}", "depends_on")

                # Search dependent repo for files related to task keywords
                keywords = self._extract_keywords(task.title)
                for key_file in getattr(dp, "key_files", []):
                    for kw in keywords:
                        if kw.lower() in key_file.lower():
                            result["cross_repo_files"].append({
                                "repo": dp.repo_name,
                                "file": key_file,
                            })
                            graph.add_node(f"file:{dp.repo_name}/{key_file}", "file", {
                                "label": f"{dp.repo_name}/{key_file}",
                            })
                            graph.add_edge(task.id, f"file:{dp.repo_name}/{key_file}", "references")
                            break

                # Add services from dependent repo
                for svc in getattr(dp, "services", []):
                    result["related_services"].append({
                        "name": svc.name,
                        "repo": dp.repo_name,
                        "path": svc.path,
                    })
                    graph.add_node(f"service:{svc.name}", "service", {
                        "label": svc.name,
                        "repo": dp.repo_name,
                    })
                    graph.add_edge(f"repo:{dp.repo_name}", f"service:{svc.name}", "contains")

            # Add workspace services
            service_map = scanner.build_service_map()
            for svc_name, svc_info in service_map.items():
                if svc_name not in [s["name"] for s in result["related_services"]]:
                    result["related_services"].append({
                        "name": svc_name,
                        "repo": svc_info.get("repo", ""),
                        "path": svc_info.get("path", ""),
                    })

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "cross_repo_analysis_complete",
                task_id=task.id,
                dependent_repos=len(result["dependent_repos"]),
                services=len(result["related_services"]),
                cross_files=len(result["cross_repo_files"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("cross_repo_analysis_failed", task_id=task.id, error=str(exc))

        return result

    @staticmethod
    def _extract_keywords(title: str) -> list[str]:
        import re
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", title)
        stop = {"the", "a", "an", "is", "are", "in", "on", "for", "to", "and", "or", "not", "of", "with"}
        return [w for w in words if len(w) > 2 and w.lower() not in stop][:15]

    def is_available(self, connectors: dict) -> bool:
        """Available when workspace profile exists."""
        from task_analyzer.knowledge.workspace_scanner import WORKSPACE_PROFILE_PATH
        return WORKSPACE_PROFILE_PATH.exists()
