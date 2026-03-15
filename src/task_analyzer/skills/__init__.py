"""
Skills package -- Reusable investigation workflows.

Skills combine multiple tools and connectors to analyze specific types
of issues. Each skill declares its required tools and runs through the
SecurityGuard -- skills cannot bypass security validation.
"""

from task_analyzer.skills.base_skill import BaseSkill
from task_analyzer.skills.skill_registry import SkillRegistry
from task_analyzer.skills.repo_analysis import RepoAnalysisSkill
from task_analyzer.skills.ticket_context import TicketContextSkill
from task_analyzer.skills.log_analysis import LogAnalysisSkill
from task_analyzer.skills.database_analysis import DatabaseAnalysisSkill
from task_analyzer.skills.cross_repo_analysis import CrossRepoAnalysisSkill
from task_analyzer.skills.database_schema import DatabaseSchemaSkill
from task_analyzer.skills.sql_query import SQLQuerySkill
from task_analyzer.skills.code_analysis import CodeAnalysisSkill

__all__ = [
    "BaseSkill",
    "SkillRegistry",
    "RepoAnalysisSkill",
    "TicketContextSkill",
    "LogAnalysisSkill",
    "DatabaseAnalysisSkill",
    "CrossRepoAnalysisSkill",
    "DatabaseSchemaSkill",
    "SQLQuerySkill",
    "CodeAnalysisSkill",
]
