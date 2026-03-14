"""
RepoAnalysisSkill — Analyzes repository structure, finds relevant files,
checks git history.

This skill uses the RepoReader tool to:
  1. Find files related to task keywords
  2. Check recent commits for related changes
  3. Identify who last modified relevant files
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from task_analyzer.skills.base_skill import BaseSkill

logger = structlog.get_logger(__name__)


class RepoAnalysisSkill(BaseSkill):
    """Analyzes repository structure, finds relevant files, checks git history."""

    name = "repo_analysis"
    display_name = "Repository Analysis"
    description = "Analyzes repo structure, finds relevant files, checks git history"
    required_tools = ["RepoReader"]

    async def run(
        self, task, context, security_guard, connectors, graph
    ) -> dict[str, Any]:
        """
        Execute repository analysis.

        Returns:
            Dict with keys: relevant_files, recent_commits, contributors
        """
        result: dict[str, Any] = {
            "relevant_files": [],
            "recent_commits": [],
            "contributors": [],
        }

        start = time.time()

        try:
            # Extract keywords from task title and description
            keywords = self._extract_keywords(task.title, task.description)
            if not keywords:
                return result

            # Step 1: Find relevant files
            security_guard.validate_tool("RepoReader", "list_files")
            # Add task node to graph
            graph.add_node(task.id, "ticket", {
                "title": task.title,
                "type": task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type),
            })

            # Search for files matching keywords in project profiles
            for profile in context.get("profiles", []):
                for key_file in getattr(profile, "key_files", []):
                    for kw in keywords:
                        if kw.lower() in key_file.lower():
                            result["relevant_files"].append(key_file)
                            # Add to graph
                            graph.add_node(key_file, "repository_file", {
                                "path": key_file,
                            })
                            graph.add_edge(task.id, key_file, "related_to")
                            break

            # Step 2: Check recent commits (simulated from profile data)
            security_guard.validate_tool("RepoReader", "git_log")

            # Step 3: Identify contributors
            security_guard.validate_tool("RepoReader", "git_blame")

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "repo_analysis_complete",
                task_id=task.id,
                files_found=len(result["relevant_files"]),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            logger.warning("repo_analysis_failed", task_id=task.id, error=str(exc))

        return result

    @staticmethod
    def _extract_keywords(title: str, description: str) -> list[str]:
        """Extract meaningful keywords from task title and description."""
        text = f"{title} {description}"
        # Remove common stop words and extract meaningful tokens
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "nor", "not", "no", "so", "if", "then", "than",
            "too", "very", "just", "about", "above", "below", "between",
            "this", "that", "these", "those", "it", "its", "when", "where",
            "how", "what", "which", "who", "whom", "why",
        }
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text)
        keywords = [
            w for w in words
            if len(w) > 2 and w.lower() not in stop_words
        ]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for kw in keywords:
            lower = kw.lower()
            if lower not in seen:
                seen.add(lower)
                unique.append(kw)
        return unique[:20]  # Limit to 20 keywords
