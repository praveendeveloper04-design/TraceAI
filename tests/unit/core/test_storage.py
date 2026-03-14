"""
Tests for the local storage layer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from task_analyzer.models.schemas import (
    InvestigationReport,
    InvestigationStatus,
    PlatformConfig,
    ProjectProfile,
)
from task_analyzer.storage.local_store import LocalStore


@pytest.fixture
def temp_store() -> LocalStore:
    """Create a LocalStore with a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield LocalStore(data_dir=Path(tmpdir))


class TestLocalStore:
    def test_save_and_load_config(self, temp_store: LocalStore) -> None:
        config = PlatformConfig(
            repositories=["/home/user/project"],
            llm_model="claude-sonnet-4-20250514",
        )
        temp_store.save_config(config)

        loaded = temp_store.load_config()
        assert loaded is not None
        assert loaded.repositories == ["/home/user/project"]
        assert loaded.llm_model == "claude-sonnet-4-20250514"

    def test_load_config_when_missing(self, temp_store: LocalStore) -> None:
        assert temp_store.load_config() is None

    def test_config_exists(self, temp_store: LocalStore) -> None:
        assert temp_store.config_exists() is False
        temp_store.save_config(PlatformConfig())
        assert temp_store.config_exists() is True

    def test_save_and_load_profile(self, temp_store: LocalStore) -> None:
        profile = ProjectProfile(
            repo_path="/home/user/project",
            repo_name="my-project",
            primary_language="Python",
        )
        temp_store.save_profile(profile)

        loaded = temp_store.load_profile("my-project")
        assert loaded is not None
        assert loaded.repo_name == "my-project"
        assert loaded.primary_language == "Python"

    def test_list_profiles(self, temp_store: LocalStore) -> None:
        for name in ["project-a", "project-b"]:
            temp_store.save_profile(ProjectProfile(
                repo_path=f"/home/user/{name}",
                repo_name=name,
            ))

        profiles = temp_store.list_profiles()
        assert len(profiles) == 2

    def test_save_and_load_investigation(self, temp_store: LocalStore) -> None:
        report = InvestigationReport(
            task_id="test-1",
            task_title="Fix bug",
            status=InvestigationStatus.COMPLETED,
            summary="Bug was caused by X.",
        )
        temp_store.save_investigation(report)

        loaded = temp_store.load_investigation(report.id)
        assert loaded is not None
        assert loaded.task_title == "Fix bug"
        assert loaded.summary == "Bug was caused by X."

    def test_list_investigations(self, temp_store: LocalStore) -> None:
        for i in range(3):
            temp_store.save_investigation(InvestigationReport(
                task_id=f"task-{i}",
                task_title=f"Task {i}",
            ))

        investigations = temp_store.list_investigations()
        assert len(investigations) == 3

    def test_cache_set_and_get(self, temp_store: LocalStore) -> None:
        temp_store.cache_set("test-key", {"data": "value"}, ttl_seconds=3600)
        result = temp_store.cache_get("test-key")
        assert result is not None
        assert result["data"] == "value"

    def test_cache_expired(self, temp_store: LocalStore) -> None:
        temp_store.cache_set("expired-key", "value", ttl_seconds=-1)
        result = temp_store.cache_get("expired-key")
        assert result is None

    def test_cache_missing(self, temp_store: LocalStore) -> None:
        assert temp_store.cache_get("nonexistent") is None
