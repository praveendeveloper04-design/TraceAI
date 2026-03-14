"""
Evidence Aggregator — Consolidates heterogeneous skill outputs into a
unified evidence structure for root cause analysis and LLM reasoning.

Performs no external operations — only normalizes in-memory data.

Performance: <10ms, deterministic output.
Security: Never calls connectors, repos, or databases directly.
"""

from __future__ import annotations

from typing import Any


class EvidenceAggregator:
    """
    Consolidates findings from investigation skills into a unified
    evidence structure used by the root cause engine and LLM reasoning.

    Operates entirely on in-memory data.
    Performance: <10ms, deterministic output.
    Security: Never calls connectors, repos, or databases directly.
    """

    def aggregate(self, skill_results: dict[str, Any]) -> dict[str, list]:
        """
        Aggregate skill results into a unified evidence structure.

        Args:
            skill_results: Dict mapping skill names to their result dicts

        Returns:
            Unified evidence dict with normalized keys
        """
        evidence: dict[str, list] = {
            "files": [],
            "commits": [],
            "contributors": [],
            "errors": [],
            "log_entries": [],
            "database_anomalies": [],
            "related_tasks": [],
        }

        # Repo skill
        repo = skill_results.get("repo_analysis", {})
        if isinstance(repo, dict):
            evidence["files"].extend(repo.get("relevant_files", []))
            evidence["commits"].extend(repo.get("recent_commits", []))
            evidence["contributors"].extend(repo.get("contributors", []))

        # Ticket context skill
        tickets = skill_results.get("ticket_context", {})
        if isinstance(tickets, dict):
            evidence["related_tasks"].extend(tickets.get("related_tasks", []))

        # Log analysis skill
        logs = skill_results.get("log_analysis", {})
        if isinstance(logs, dict):
            evidence["errors"].extend(logs.get("error_patterns", []))
            evidence["log_entries"].extend(logs.get("log_entries", []))

        # Database analysis skill
        db = skill_results.get("database_analysis", {})
        if isinstance(db, dict):
            evidence["database_anomalies"].extend(db.get("anomalies", []))

        return evidence

    def summarize(self, evidence: dict[str, list]) -> str:
        """
        Produce a human-readable summary of the evidence for LLM context.

        Args:
            evidence: Unified evidence dict from aggregate()

        Returns:
            Markdown-formatted summary string
        """
        parts: list[str] = ["## Evidence Summary"]

        if evidence.get("files"):
            parts.append(f"\n### Relevant Files ({len(evidence['files'])})")
            for f in evidence["files"][:10]:
                parts.append(f"- `{f}`")

        if evidence.get("commits"):
            parts.append(f"\n### Recent Commits ({len(evidence['commits'])})")
            for c in evidence["commits"][:5]:
                if isinstance(c, dict):
                    sha = c.get("sha", c.get("id", "unknown"))
                    msg = c.get("message", "")
                    parts.append(f"- `{sha}`: {msg[:100]}")
                else:
                    parts.append(f"- {c}")

        if evidence.get("contributors"):
            parts.append(f"\n### Contributors ({len(evidence['contributors'])})")
            for contrib in evidence["contributors"][:5]:
                parts.append(f"- {contrib}")

        if evidence.get("errors"):
            parts.append(f"\n### Error Patterns ({len(evidence['errors'])})")
            for err in evidence["errors"][:5]:
                parts.append(f"- {str(err)[:200]}")

        if evidence.get("log_entries"):
            parts.append(f"\n### Log Entries ({len(evidence['log_entries'])})")
            for entry in evidence["log_entries"][:5]:
                if isinstance(entry, dict):
                    parts.append(
                        f"- [{entry.get('level', '?')}] {entry.get('message', '')[:150]}"
                    )
                else:
                    parts.append(f"- {entry}")

        if evidence.get("database_anomalies"):
            parts.append(
                f"\n### Database Anomalies ({len(evidence['database_anomalies'])})"
            )
            for anomaly in evidence["database_anomalies"][:5]:
                parts.append(f"- {str(anomaly)[:200]}")

        if evidence.get("related_tasks"):
            parts.append(f"\n### Related Tasks ({len(evidence['related_tasks'])})")
            for rt in evidence["related_tasks"][:5]:
                if isinstance(rt, dict):
                    parts.append(
                        f"- {rt.get('id', '?')}: {rt.get('title', 'Unknown')}"
                    )
                else:
                    parts.append(f"- {rt}")

        return "\n".join(parts)
