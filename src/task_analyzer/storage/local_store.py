"""
Storage — Local persistence for configuration, profiles, and investigation reports.

Uses SQLite for structured data and JSON files for configuration.
All data is stored under ``~/.task-analyzer/``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.models.schemas import (
    InvestigationReport,
    PlatformConfig,
    ProjectProfile,
)

logger = structlog.get_logger(__name__)

DEFAULT_DATA_DIR = Path.home() / ".task-analyzer"


class LocalStore:
    """
    File-based local storage for Task Analyzer data.

    Layout::

        ~/.task-analyzer/
        ├── config.json              # Platform configuration (no secrets)
        ├── profiles/                # Project knowledge profiles
        │   └── <repo-name>.json
        ├── investigations/          # Investigation reports
        │   └── <id>.json
        └── cache/                   # Ephemeral cache
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.config_path = self.data_dir / "config.json"
        self.profiles_dir = self.data_dir / "profiles"
        self.investigations_dir = self.data_dir / "investigations"
        self.cache_dir = self.data_dir / "cache"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in [self.data_dir, self.profiles_dir, self.investigations_dir, self.cache_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Platform Config ───────────────────────────────────────────────────

    def save_config(self, config: PlatformConfig) -> None:
        """Persist platform configuration (never contains secrets)."""
        config.updated_at = datetime.utcnow()
        self.config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        logger.info("config_saved", path=str(self.config_path))

    def load_config(self) -> PlatformConfig | None:
        """Load platform configuration, or None if not yet initialized."""
        if not self.config_path.exists():
            return None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return PlatformConfig.model_validate(data)
        except Exception as exc:
            logger.error("config_load_failed", error=str(exc))
            return None

    def config_exists(self) -> bool:
        return self.config_path.exists()

    # ── Project Profiles ──────────────────────────────────────────────────

    def save_profile(self, profile: ProjectProfile) -> None:
        """Save a project knowledge profile."""
        filename = _safe_filename(profile.repo_name) + ".json"
        path = self.profiles_dir / filename
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        logger.info("profile_saved", repo=profile.repo_name, path=str(path))

    def load_profile(self, repo_name: str) -> ProjectProfile | None:
        """Load a project profile by repository name."""
        filename = _safe_filename(repo_name) + ".json"
        path = self.profiles_dir / filename
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ProjectProfile.model_validate(data)
        except Exception as exc:
            logger.error("profile_load_failed", repo=repo_name, error=str(exc))
            return None

    def list_profiles(self) -> list[str]:
        """List all stored profile names."""
        return [p.stem for p in self.profiles_dir.glob("*.json")]

    # ── Investigation Reports ─────────────────────────────────────────────

    def save_investigation(self, report: InvestigationReport) -> None:
        """Persist an investigation report."""
        path = self.investigations_dir / f"{report.id}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        logger.info("investigation_saved", id=report.id, task=report.task_id)

    def load_investigation(self, report_id: str) -> InvestigationReport | None:
        """Load an investigation report by ID."""
        path = self.investigations_dir / f"{report_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return InvestigationReport.model_validate(data)
        except Exception as exc:
            logger.error("investigation_load_failed", id=report_id, error=str(exc))
            return None

    def list_investigations(self) -> list[dict[str, Any]]:
        """List all investigations with summary info."""
        results = []
        for path in sorted(self.investigations_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "id": data.get("id"),
                    "task_id": data.get("task_id"),
                    "task_title": data.get("task_title"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                })
            except Exception:
                continue
        return results

    # ── Cache ─────────────────────────────────────────────────────────────

    def cache_set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        """Write a value to the ephemeral cache."""
        path = self.cache_dir / f"{_safe_filename(key)}.json"
        payload = {
            "value": value,
            "expires_at": (datetime.utcnow().timestamp() + ttl_seconds),
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")

    def cache_get(self, key: str) -> Any | None:
        """Read from cache, returning None if missing or expired."""
        path = self.cache_dir / f"{_safe_filename(key)}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if datetime.utcnow().timestamp() > payload.get("expires_at", 0):
                path.unlink(missing_ok=True)
                return None
            return payload.get("value")
        except Exception:
            return None


def _safe_filename(name: str) -> str:
    """Convert a string into a filesystem-safe filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


# Module-level singleton
local_store = LocalStore()
