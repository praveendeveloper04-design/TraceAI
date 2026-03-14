"""
Tests for the data models.
"""

from __future__ import annotations

from datetime import datetime

from task_analyzer.models.schemas import (
    ConnectorConfig,
    ConnectorType,
    InvestigationFinding,
    InvestigationReport,
    InvestigationStatus,
    PlatformConfig,
    ProjectProfile,
    ServiceInfo,
    Severity,
    Task,
    TaskComment,
    TaskStatus,
    TaskType,
)


class TestTask:
    def test_create_minimal_task(self) -> None:
        task = Task(
            id="test-1",
            source=ConnectorType.JIRA,
            external_id="PROJ-123",
            title="Fix login bug",
        )
        assert task.id == "test-1"
        assert task.source == ConnectorType.JIRA
        assert task.task_type == TaskType.UNKNOWN
        assert task.severity == Severity.UNKNOWN

    def test_full_context(self) -> None:
        task = Task(
            id="test-1",
            source=ConnectorType.AZURE_DEVOPS,
            external_id="12345",
            title="Critical production bug",
            description="Users cannot log in after the latest deployment.",
            task_type=TaskType.BUG,
            severity=Severity.CRITICAL,
            status=TaskStatus.ACTIVE,
            tags=["production", "auth"],
            comments=[
                TaskComment(
                    author="Alice",
                    content="This started after deploy v2.3.1",
                    created_at=datetime(2024, 1, 15),
                ),
            ],
        )
        context = task.full_context
        assert "Critical production bug" in context
        assert "Users cannot log in" in context
        assert "Alice" in context
        assert "production" in context

    def test_task_serialization(self) -> None:
        task = Task(
            id="test-1",
            source=ConnectorType.GITHUB_ISSUES,
            external_id="42",
            title="Add dark mode",
        )
        data = task.model_dump()
        restored = Task.model_validate(data)
        assert restored.id == task.id
        assert restored.title == task.title


class TestProjectProfile:
    def test_context_summary(self) -> None:
        profile = ProjectProfile(
            repo_path="/home/user/project",
            repo_name="my-project",
            primary_language="Python",
            languages={"Python": 0.7, "JavaScript": 0.2, "SQL": 0.1},
            services=[
                ServiceInfo(name="api", path="src/api", description="REST API"),
                ServiceInfo(name="worker", path="src/worker", description="Background worker"),
            ],
            directory_tree="my-project/\n├── src/\n│   ├── api/\n│   └── worker/",
        )
        summary = profile.context_summary
        assert "my-project" in summary
        assert "Python" in summary
        assert "api" in summary
        assert "worker" in summary


class TestInvestigationReport:
    def test_to_markdown(self) -> None:
        report = InvestigationReport(
            task_id="test-1",
            task_title="Fix login bug",
            status=InvestigationStatus.COMPLETED,
            summary="The login bug is caused by a missing null check.",
            root_cause="Missing null check in auth middleware.",
            findings=[
                InvestigationFinding(
                    category="root_cause",
                    title="Null pointer in auth middleware",
                    description="The auth middleware does not check for null tokens.",
                    confidence=0.9,
                    file_references=["src/middleware/auth.py"],
                    evidence=["Stack trace shows NPE at line 42"],
                ),
            ],
            recommendations=["Add null check before token validation"],
            affected_files=["src/middleware/auth.py"],
            model_used="claude-sonnet-4-20250514",
        )
        md = report.to_markdown()
        assert "Fix login bug" in md
        assert "root_cause" in md
        assert "auth.py" in md
        assert "90%" in md


class TestPlatformConfig:
    def test_default_config(self) -> None:
        config = PlatformConfig()
        assert config.version == "1.0"
        assert config.repositories == []
        assert config.ticket_source is None
        assert config.llm_model == "claude-sonnet-4-20250514"

    def test_config_with_ticket_source(self) -> None:
        config = PlatformConfig(
            repositories=["/home/user/project"],
            ticket_source=ConnectorConfig(
                connector_type=ConnectorType.JIRA,
                name="jira",
                settings={"base_url": "https://example.atlassian.net"},
            ),
        )
        assert config.ticket_source is not None
        assert config.ticket_source.connector_type == ConnectorType.JIRA
