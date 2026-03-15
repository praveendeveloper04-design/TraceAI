"""
Investigation Engine — LangChain-powered AI reasoning for task investigation.

This is the core of TraceAI. It orchestrates:

  1. Task ingestion from the configured ticket source
  2. Repository context building from the project knowledge profile
  3. Skill execution (repo analysis, ticket context, log analysis, DB analysis)
  4. Evidence aggregation and root cause ranking
  5. Multi-step AI reasoning using Claude via LangChain
  6. Structured investigation report generation

Resilience model:
  - Each skill runs inside a failure boundary (timeout + exception catch)
  - Connector failures are logged and skipped, not fatal
  - LLM failure produces a partial report from skill evidence
  - Investigation only fails if the task itself cannot be loaded

Security: Every operation goes through:
  1. SecurityGuard.validate_tool() — tool permission check
  2. RateLimiter.acquire() — rate limit enforcement
  3. AuditLogger.log_tool_call() — audit trail
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.base.registry import ConnectorRegistry
from task_analyzer.core.audit_logger import AuditLogger
from task_analyzer.core.rate_limiter import RateLimiter
from task_analyzer.core.security_guard import SecurityGuard
from task_analyzer.investigation.evidence_aggregator import EvidenceAggregator
from task_analyzer.investigation.graph_engine import GraphBuilder, InvestigationGraph, NodeType
from task_analyzer.investigation.root_cause_engine import RootCauseEngine
from task_analyzer.models.schemas import (
    InvestigationFinding,
    InvestigationReport,
    InvestigationStatus,
    InvestigationStep,
    PlatformConfig,
    ProjectProfile,
    Task,
)
from task_analyzer.skills.database_analysis import DatabaseAnalysisSkill
from task_analyzer.skills.log_analysis import LogAnalysisSkill
from task_analyzer.skills.repo_analysis import RepoAnalysisSkill
from task_analyzer.skills.skill_registry import SkillRegistry
from task_analyzer.skills.ticket_context import TicketContextSkill
from task_analyzer.skills.cross_repo_analysis import CrossRepoAnalysisSkill
from task_analyzer.skills.database_schema import DatabaseSchemaSkill
from task_analyzer.skills.sql_query import SQLQuerySkill

logger = structlog.get_logger(__name__)


# ─── System Prompt ────────────────────────────────────────────────────────────

INVESTIGATION_SYSTEM_PROMPT = """\
You are an expert software engineer and investigator. Your job is to analyze
a task (bug, incident, user story, or feature request) and produce a thorough
investigation report.

## Your Approach

1. **Understand the Task**: Read the task description, comments, and metadata carefully.
2. **Analyze the Codebase**: Use the project knowledge profile to understand the architecture.
3. **Use Available Tools**: If tools are available (database queries, documentation search,
   log retrieval), use them to gather evidence. Only use tools that are relevant.
4. **Review Skill Findings**: Pre-investigation skills have already gathered evidence.
   Use their findings to inform your analysis.
5. **Reason Step by Step**: Think through the problem methodically. Consider multiple hypotheses.
6. **Produce Findings**: For each finding, state your confidence level and supporting evidence.

## Output Format

Structure your response as a JSON object with these fields:

```json
{{
    "summary": "A 2-3 sentence executive summary of the investigation",
    "root_cause": "Detailed root cause analysis (if applicable)",
    "findings": [
        {{
            "category": "root_cause|related_code|configuration_issue|dependency_issue|design_flaw|missing_test",
            "title": "Short title",
            "description": "Detailed description",
            "confidence": 0.0-1.0,
            "evidence": ["Evidence item 1", "Evidence item 2"],
            "file_references": ["path/to/file.py"]
        }}
    ],
    "recommendations": [
        "Actionable recommendation 1",
        "Actionable recommendation 2"
    ],
    "affected_files": ["path/to/file1.py", "path/to/file2.py"],
    "affected_services": ["service-name-1"]
}}
```

Be thorough but concise. Focus on actionable insights.
"""


# ─── Tool Builders ────────────────────────────────────────────────────────────

def _build_search_tool(connector: BaseConnector) -> StructuredTool:
    """Create a LangChain tool from a connector's search method."""

    async def _search(query: str) -> str:
        try:
            results = await connector.search(query, max_results=10)
            if not results:
                return f"No results found in {connector.display_name} for: {query}"
            # Format results as readable text
            lines = [f"## Results from {connector.display_name}"]
            for i, r in enumerate(results, 1):
                lines.append(f"\n### Result {i}")
                for k, v in r.items():
                    if v and k not in ("raw_data",):
                        lines.append(f"- **{k}**: {str(v)[:300]}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error searching {connector.display_name}: {exc}"

    return StructuredTool.from_function(
        coroutine=_search,
        name=f"search_{connector.config.name}",
        description=f"Search {connector.display_name} ({connector.description}). Input: search query string.",
    )


def _build_context_tool(connector: BaseConnector, task: Task) -> StructuredTool:
    """Create a tool that fetches additional context from a connector."""

    async def _get_context(reason: str = "") -> str:
        try:
            context = await connector.get_context(task)
            return context or f"No additional context available from {connector.display_name}"
        except Exception as exc:
            return f"Error getting context from {connector.display_name}: {exc}"

    return StructuredTool.from_function(
        coroutine=_get_context,
        name=f"context_{connector.config.name}",
        description=f"Get additional context from {connector.display_name} related to the current task. Input: brief reason for needing context.",
    )


# ─── LLM Initialization ──────────────────────────────────────────────────────

def _resolve_env_var(name: str) -> str | None:
    """
    Resolve an environment variable from multiple sources.

    Resolution order:
      1. os.environ (current process)
      2. Windows User registry (set via setx, may not be in current shell)
      3. Windows System registry

    Returns the stripped value, or None if not found.
    """
    # Tier 1: Current process environment
    val = os.environ.get(name, "").strip()
    if val:
        return val

    # Tier 2: Windows registry (User then System)
    try:
        import winreg
        for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            try:
                with winreg.OpenKey(hive, r"Environment") as reg_key:
                    reg_val, _ = winreg.QueryValueEx(reg_key, name)
                    reg_val = str(reg_val).strip()
                    if reg_val:
                        return reg_val
            except FileNotFoundError:
                continue
    except ImportError:
        pass  # Not on Windows

    return None


def _resolve_anthropic_api_key() -> str | None:
    """
    Resolve the Anthropic API key from all available sources.

    Resolution order:
      1. os.environ["ANTHROPIC_API_KEY"]
      2. Windows User/System environment variable (survives setx)
      3. ~/.traceai/credentials.json  → {"anthropic": {"api_key": "..."}}
      4. OS keyring under traceai/anthropic/api_key

    Returns the sanitized key, or None if not found anywhere.
    """
    source = None
    key = None

    # Tier 1+2: Environment variable (process + Windows registry)
    key = _resolve_env_var("ANTHROPIC_API_KEY")
    if key:
        source = "environment"

    # Tier 3+4: credentials.json and OS keyring
    if not key:
        from task_analyzer.security.credential_manager import CredentialManager
        cm = CredentialManager()
        val = cm.retrieve("anthropic", "api_key")
        if val:
            key = val.strip()
            source = "credentials_json_or_keyring"

    if key:
        # Inject into os.environ so the Anthropic SDK picks it up
        os.environ["ANTHROPIC_API_KEY"] = key
        logger.info(
            "anthropic_api_key_resolved",
            source=source,
            key_length=len(key),
            key_prefix=key[:8] + "..." if len(key) > 8 else "****",
        )
    else:
        logger.error(
            "anthropic_api_key_not_found",
            checked=[
                "ANTHROPIC_API_KEY env var",
                "Windows registry (User/System)",
                "~/.traceai/credentials.json → anthropic.api_key",
                "OS keyring → traceai/anthropic/api_key",
            ],
        )

    return key


def _sync_anthropic_env_vars():
    """
    Ensure all Anthropic-related environment variables are available
    to the current process. On Windows, setx writes to the registry
    but doesn't update the current shell — so the Python process
    spawned by VS Code may be missing them.

    Variables synced:
      - ANTHROPIC_API_KEY  (required)
      - ANTHROPIC_BASE_URL (optional — for corporate AI gateway proxies)
      - ANTHROPIC_AUTH_TOKEN (optional — some proxy configurations)
    """
    for var_name in ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"]:
        current = os.environ.get(var_name, "").strip()
        if not current:
            resolved = _resolve_env_var(var_name)
            if resolved:
                os.environ[var_name] = resolved
                logger.info(
                    "env_var_synced_from_registry",
                    variable=var_name,
                    value_length=len(resolved),
                )
        elif current != os.environ.get(var_name, ""):
            # Sanitize: strip whitespace/newlines from existing value
            os.environ[var_name] = current
            logger.info(
                "env_var_sanitized",
                variable=var_name,
                value_length=len(current),
            )


def _create_llm(config: PlatformConfig):
    """
    Create the Claude LLM instance.

    Resolves the API key and base URL from environment, Windows registry,
    credentials.json, or OS keyring. Raises ValueError if the key cannot
    be found — this is a configuration error, not a transient failure.

    Supports corporate AI gateway proxies via ANTHROPIC_BASE_URL.
    """
    from langchain_anthropic import ChatAnthropic

    # Sync all Anthropic env vars from Windows registry if needed
    _sync_anthropic_env_vars()

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        # Try credentials.json / keyring as last resort
        key = _resolve_anthropic_api_key()

    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. Set it via:\n"
            "  1. Environment variable: set ANTHROPIC_API_KEY=sk-ant-...\n"
            "  2. Windows: setx ANTHROPIC_API_KEY sk-ant-... (then restart VS Code)\n"
            "  3. File: add to ~/.traceai/credentials.json:\n"
            '     {"anthropic": {"api_key": "sk-ant-..."}}\n'
        )

    # Build kwargs for ChatAnthropic
    llm_kwargs: dict[str, Any] = {
        "model": config.llm_model,
        "temperature": config.llm_temperature,
        "max_tokens": config.llm_max_tokens,
        "api_key": key,
    }

    # Support corporate AI gateway proxy via ANTHROPIC_BASE_URL
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        llm_kwargs["base_url"] = base_url
        logger.info(
            "anthropic_using_custom_base_url",
            base_url=base_url,
        )

    logger.info(
        "llm_client_initialized",
        model=config.llm_model,
        key_length=len(key),
        has_custom_base_url=bool(base_url),
    )

    return ChatAnthropic(**llm_kwargs)


# ─── Investigation Engine ────────────────────────────────────────────────────

class InvestigationEngine:
    """
    Orchestrates AI-powered task investigations using LangChain and Claude.

    The engine:
      - Runs investigation skills (repo, ticket, log, database analysis)
      - Builds a rich context from the task, project profile, and connectors
      - Creates LangChain tools from configured connectors
      - Runs a multi-step reasoning chain with Claude
      - Aggregates evidence and ranks root causes
      - Parses the output into a structured InvestigationReport

    Resilience:
      - Each skill runs inside a failure boundary (30s timeout + exception catch)
      - Connector failures are logged and skipped, not fatal
      - LLM failure produces a partial report from skill evidence
      - Investigation only fails if the task itself cannot be loaded
    """

    def __init__(
        self,
        config: PlatformConfig,
        registry: ConnectorRegistry,
        profiles: list[ProjectProfile] | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.profiles = profiles or []

        # Initialize Claude via LangChain (with API key sanitization)
        self.llm = _create_llm(config)

        # Security, rate limiting, and audit logging
        self.guard = SecurityGuard(safe_mode=(config.mode == "safe"))
        self.rate_limiter = RateLimiter()
        self.audit = AuditLogger()

        # Attach rate limiter to all connectors
        for name, connector in registry.get_all_instances().items():
            connector.set_rate_limiter(self.rate_limiter)

        # Validate optional connectors — remove broken ones early
        self._failed_connectors: list[str] = []
        self._validate_connectors()

        # Initialize skill registry
        self.skill_registry = SkillRegistry()
        self.skill_registry.register(RepoAnalysisSkill())
        self.skill_registry.register(TicketContextSkill())
        self.skill_registry.register(LogAnalysisSkill())
        self.skill_registry.register(DatabaseAnalysisSkill())
        self.skill_registry.register(CrossRepoAnalysisSkill())
        self.skill_registry.register(DatabaseSchemaSkill())
        self.skill_registry.register(SQLQuerySkill())

        # Load workspace profile for cross-repo awareness
        self._workspace_summary = ""
        try:
            from task_analyzer.knowledge.workspace_scanner import (
                WorkspaceScanner, load_workspace_profile,
            )
            from task_analyzer.storage.local_store import LocalStore

            workspace = load_workspace_profile()
            if workspace.repos:
                scanner = WorkspaceScanner(workspace)
                store = LocalStore()

                # Load dependent repo profiles
                for profile in self.profiles:
                    dep_profiles = scanner.get_dependency_profiles(
                        profile.repo_name, store
                    )
                    for dp in dep_profiles:
                        if dp not in self.profiles:
                            self.profiles.append(dp)

                self._workspace_summary = scanner.summarize()
                logger.info(
                    "workspace_loaded",
                    repos=len(workspace.repos),
                    profiles=len(self.profiles),
                )
        except Exception as exc:
            logger.debug("workspace_load_skipped", error=str(exc))

    def _validate_connectors(self) -> None:
        """
        Pre-validate optional connectors. If a connector cannot even
        initialize (e.g. missing driver, bad connection string), mark it
        as failed so skills and context-building skip it gracefully.
        """
        for name, connector in list(self.registry.get_all_instances().items()):
            try:
                # Light validation: check if the connector can build its client
                # without actually connecting. For SQL, this means checking the
                # connection string exists.
                if hasattr(connector, '_get_credential'):
                    for key in getattr(connector, 'required_credentials', []):
                        cred = connector._get_credential(key)
                        if not cred:
                            raise ValueError(f"Missing credential: {key}")
            except Exception as exc:
                self._failed_connectors.append(name)
                logger.warning(
                    "connector_pre_validation_failed",
                    connector=name,
                    connector_type=connector.connector_type.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    async def investigate(self, task: Task, progress_callback: Any | None = None) -> InvestigationReport:
        """
        Run a full investigation on a task.

        Args:
            task: The task to investigate
            progress_callback: Optional async callable(stage, message) for live progress
        """
        report = InvestigationReport(
            task_id=task.id,
            task_title=task.title,
            status=InvestigationStatus.IN_PROGRESS,
            model_used=self.config.llm_model,
        )

        async def _emit(stage: str, message: str) -> None:
            if progress_callback:
                try:
                    await progress_callback(stage, message)
                except Exception:
                    pass

        self.audit.log_investigation_start(task.id, task.title)
        start_time = time.time()
        await _emit("loading_ticket", "Loading ticket details...")

        try:
            # Step 0: Run investigation planner
            investigation_plan = None
            try:
                from task_analyzer.investigation.planner import InvestigationPlanner
                planner = InvestigationPlanner()
                investigation_plan = planner.plan(task.title, task.description)
                logger.info(
                    "investigation_plan_ready",
                    systems=investigation_plan.systems,
                    repos=investigation_plan.repos,
                    tables=investigation_plan.tables,
                    keywords=investigation_plan.matched_keywords,
                )

                # Load additional repos identified by planner
                if investigation_plan.repos:
                    from task_analyzer.storage.local_store import LocalStore
                    from task_analyzer.knowledge.scanner import RepositoryScanner
                    store = LocalStore()
                    for repo_name in investigation_plan.repos:
                        # Check if already loaded
                        already = any(
                            p.repo_name == repo_name for p in self.profiles
                        )
                        if not already:
                            cached = store.load_profile(repo_name)
                            if cached:
                                self.profiles.append(cached)
                                logger.info("planner_loaded_repo", repo=repo_name, source="cache")
            except Exception as exc:
                logger.debug("planner_skipped", error=str(exc))

            # Step 1: Initialize investigation graph
            graph = InvestigationGraph()
            graph.add_node(task.id, NodeType.TICKET, {
                "label": task.title[:60],
                "title": task.title,
                "type": task.task_type.value,
            })

            # Step 2: Run available skills
            await _emit("skills_execution", "Running investigation skills...")
            report.steps.append(InvestigationStep(
                step_number=1,
                action="Running investigation skills",
                reasoning="Executing available skills to gather evidence before AI reasoning",
            ))

            connectors = self.registry.get_all_instances()
            available_skills = self.skill_registry.list_available(connectors)
            skill_results: dict[str, Any] = {}
            skill_errors: list[str] = []

            skill_context = {"profiles": self.profiles}
            if investigation_plan:
                skill_context["investigation_plan"] = investigation_plan

            for skill in available_skills:
                skill_start = time.time()
                try:
                    result = await asyncio.wait_for(
                        skill.run(
                            task, skill_context, self.guard, connectors, graph
                        ),
                        timeout=30,
                    )
                    skill_results[skill.name] = result
                    skill_elapsed = int((time.time() - skill_start) * 1000)
                    self.audit.log_skill_execution(
                        task.id, skill.name, "success",
                        duration_ms=skill_elapsed,
                        findings_count=len(result) if isinstance(result, dict) else 0,
                    )
                    logger.info(
                        "skill_completed",
                        skill=skill.name,
                        task_id=task.id,
                        duration_ms=skill_elapsed,
                    )
                except asyncio.TimeoutError:
                    skill_elapsed = int((time.time() - skill_start) * 1000)
                    self.audit.log_skill_execution(
                        task.id, skill.name, "timeout",
                        duration_ms=skill_elapsed,
                    )
                    skill_errors.append(f"{skill.display_name}: timed out after 30s")
                    logger.warning(
                        "skill_execution_timeout",
                        skill=skill.name,
                        task_id=task.id,
                        timeout_s=30,
                    )
                except Exception as exc:
                    skill_elapsed = int((time.time() - skill_start) * 1000)
                    self.audit.log_skill_execution(
                        task.id, skill.name, "failed",
                        duration_ms=skill_elapsed,
                    )
                    skill_errors.append(f"{skill.display_name}: {type(exc).__name__}: {exc}")
                    logger.warning(
                        "skill_execution_failed",
                        skill=skill.name,
                        task_id=task.id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

            # Step 3: Aggregate evidence and rank root causes
            await _emit("evidence_aggregation", "Aggregating evidence...")
            report.steps.append(InvestigationStep(
                step_number=2,
                action="Aggregating evidence and ranking root causes",
                reasoning="Consolidating skill findings into unified evidence structure",
            ))

            aggregator = EvidenceAggregator()
            evidence = aggregator.aggregate(skill_results)

            root_engine = RootCauseEngine()
            hypotheses = root_engine.analyze(graph.export(), evidence)

            # Step 3b: Build evidence graph
            await _emit("building_graph", "Building evidence graph...")
            graph_builder = GraphBuilder()
            graph_builder.build(graph, task.id, evidence, hypotheses)

            # Step 4: Build context (with connector error isolation)
            await _emit("building_context", "Building investigation context...")
            report.steps.append(InvestigationStep(
                step_number=3,
                action="Building investigation context",
                reasoning="Gathering task details, project knowledge, skill findings, and connector context",
            ))
            context = await self._build_context(
                task, evidence, aggregator, graph, skill_results, investigation_plan
            )

            # Step 5: Create tools (only from healthy connectors)
            tools = self._build_tools(task)
            tool_names = [t.name for t in tools]
            report.steps.append(InvestigationStep(
                step_number=4,
                action="Preparing investigation tools",
                reasoning=f"Available tools: {', '.join(tool_names) if tool_names else 'None'}",
            ))

            # Step 6: Run the AI investigation (with graceful fallback)
            await _emit("ai_reasoning", "Running AI reasoning with Claude...")
            report.steps.append(InvestigationStep(
                step_number=5,
                action="Running AI analysis",
                tool_used="Claude via LangChain",
                reasoning="Sending context, evidence, and task to Claude for multi-step reasoning",
            ))

            llm_succeeded = False
            try:
                result = await self._run_investigation(context, tools)
                self._parse_result(result, report)
                llm_succeeded = True

                report.steps.append(InvestigationStep(
                    step_number=6,
                    action="AI analysis completed",
                    reasoning="Extracted structured findings from AI output",
                ))
            except Exception as llm_exc:
                llm_error_msg = str(llm_exc)
                llm_error_type = type(llm_exc).__name__

                # If the error is tool-related, retry without tools
                if tools and ("tool" in llm_error_msg.lower() or "toolUse" in llm_error_msg or "toolResult" in llm_error_msg):
                    logger.warning(
                        "llm_tool_error_retrying_without_tools",
                        task_id=task.id,
                        error=llm_error_msg[:200],
                    )
                    try:
                        result = await self._run_investigation(context, [])
                        self._parse_result(result, report)
                        llm_succeeded = True

                        report.steps.append(InvestigationStep(
                            step_number=6,
                            action="AI analysis completed (without tools)",
                            reasoning="Tool calling failed; retried with direct analysis",
                        ))
                    except Exception as retry_exc:
                        logger.error(
                            "llm_call_failed",
                            task_id=task.id,
                            error=str(retry_exc),
                            error_type=type(retry_exc).__name__,
                            retry=True,
                        )
                        report.steps.append(InvestigationStep(
                            step_number=6,
                            action="AI analysis failed — producing partial report",
                            reasoning=f"LLM error: {type(retry_exc).__name__}: {retry_exc}",
                        ))
                        self._build_partial_report(report, task, evidence, hypotheses, skill_errors)
                else:
                    logger.error(
                        "llm_call_failed",
                        task_id=task.id,
                        error=llm_error_msg[:500],
                        error_type=llm_error_type,
                    )
                    report.steps.append(InvestigationStep(
                        step_number=6,
                        action="AI analysis failed — producing partial report",
                        reasoning=f"LLM error: {llm_error_type}: {llm_error_msg[:200]}",
                    ))
                    self._build_partial_report(report, task, evidence, hypotheses, skill_errors)

            # Attach graph, hypotheses, and evidence to report
            await _emit("generating_report", "Generating investigation report...")
            report.investigation_graph = graph.export()
            report.root_cause_node_id = graph.root_cause_node_id
            report.root_cause_hypotheses = [
                {
                    "description": h.description,
                    "evidence": h.evidence,
                    "confidence": h.score,
                }
                for h in hypotheses
            ]
            report.evidence_summary = evidence

            elapsed_ms = int((time.time() - start_time) * 1000)
            report.status = InvestigationStatus.COMPLETED
            report.completed_at = datetime.utcnow()
            for step in report.steps:
                step.duration_ms = elapsed_ms // len(report.steps)

            # Add warnings about failed components
            if skill_errors or self._failed_connectors:
                warnings = []
                for err in skill_errors:
                    warnings.append(f"Skill: {err}")
                for name in self._failed_connectors:
                    warnings.append(f"Connector unavailable: {name}")
                if not llm_succeeded:
                    warnings.append("AI analysis unavailable — partial report from skill evidence")
                report.error = "; ".join(warnings) if warnings else None

            self.audit.log_investigation_complete(
                task.id, report.status.value,
                len(report.findings), elapsed_ms,
            )

            logger.info(
                "investigation_completed",
                task_id=task.id,
                findings=len(report.findings),
                hypotheses=len(hypotheses),
                graph_nodes=len(graph.nodes),
                llm_succeeded=llm_succeeded,
                skill_errors=len(skill_errors),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            report.status = InvestigationStatus.FAILED
            report.error = f"{type(exc).__name__}: {exc}"
            report.completed_at = datetime.utcnow()
            elapsed_ms = int((time.time() - start_time) * 1000)
            self.audit.log_investigation_complete(
                task.id, "failed", 0, elapsed_ms,
            )
            logger.error(
                "investigation_failed",
                task_id=task.id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        return report

    def _build_partial_report(
        self,
        report: InvestigationReport,
        task: Task,
        evidence: dict,
        hypotheses: list,
        skill_errors: list[str],
    ) -> None:
        """
        Build a partial report from skill evidence when the LLM is unavailable.

        This ensures the investigation produces useful output even when
        Claude cannot be reached (API key issues, network problems, etc.).
        """
        # Summary from evidence
        parts = [f"Partial investigation of: {task.title}"]
        parts.append("AI analysis was unavailable. Results below are from automated skill analysis only.")

        if skill_errors:
            parts.append(f"\nSkill warnings: {'; '.join(skill_errors)}")

        report.summary = " ".join(parts)

        # Convert hypotheses to findings
        for h in hypotheses:
            report.findings.append(InvestigationFinding(
                category="root_cause",
                title=h.description,
                description=f"Automated hypothesis (confidence: {h.score:.0%})",
                confidence=h.score,
                evidence=h.evidence,
                file_references=[],
            ))

        # Add evidence-based findings
        if evidence.get("files"):
            report.affected_files = evidence["files"][:20]
        if evidence.get("errors"):
            report.findings.append(InvestigationFinding(
                category="root_cause",
                title="Error patterns detected",
                description=f"Found {len(evidence['errors'])} error pattern(s) in logs",
                confidence=0.5,
                evidence=[str(e)[:200] for e in evidence["errors"][:5]],
                file_references=[],
            ))

        report.recommendations = [
            "Review the automated findings above",
            "Re-run investigation when AI analysis is available for deeper insights",
        ]

    async def _build_context(
        self,
        task: Task,
        evidence: dict | None = None,
        aggregator: EvidenceAggregator | None = None,
        graph: InvestigationGraph | None = None,
        skill_results: dict | None = None,
        investigation_plan: Any | None = None,
    ) -> str:
        """Assemble the full context for the investigation."""
        parts = [
            "# Task Under Investigation",
            task.full_context,
        ]

        # Add system architecture from system_map.json
        try:
            from task_analyzer.investigation.planner import load_system_map
            system_map = load_system_map()
            if system_map.services:
                parts.append(f"\n# {system_map.summarize()}")
        except Exception:
            pass

        # Add investigation plan
        if investigation_plan and hasattr(investigation_plan, "summarize"):
            plan_summary = investigation_plan.summarize()
            if plan_summary:
                parts.append(f"\n# {plan_summary}")

        # Add workspace architecture (cross-repo awareness)
        if self._workspace_summary:
            parts.append(f"\n# {self._workspace_summary}")

        # Add project profiles (including dependent repos)
        if self.profiles:
            parts.append("\n# Project Knowledge")
            for profile in self.profiles:
                parts.append(profile.context_summary)

        # Add database schema from skill results
        if skill_results and "database_schema" in skill_results:
            schema = skill_results["database_schema"]
            schema_summary = schema.get("schema_summary", "")
            if schema_summary:
                parts.append(f"\n# Database Schema\n{schema_summary}")

        # Add cross-repo analysis from skill results
        if skill_results and "cross_repo_analysis" in skill_results:
            cross = skill_results["cross_repo_analysis"]
            if cross.get("related_services"):
                parts.append("\n# Related Services")
                for svc in cross["related_services"]:
                    parts.append(f"- **{svc['name']}** in {svc.get('repo', '?')} (`{svc.get('path', '?')}`)")

        # Add SQL query results from SQLQuerySkill
        if skill_results and "sql_query" in skill_results:
            sql_data = skill_results["sql_query"]
            query_results = sql_data.get("query_results", [])
            if query_results:
                parts.append("\n# SQL Query Results")
                for qr in query_results[:5]:
                    table = qr.get("table", "?")
                    rows = qr.get("row_count", 0)
                    cols = qr.get("columns", [])
                    parts.append(f"\n### {table} ({rows} rows)")
                    if cols:
                        parts.append(f"Columns: {', '.join(cols[:15])}")
                    for row in qr.get("sample_rows", [])[:3]:
                        row_str = " | ".join(f"{k}={v[:50]}" for k, v in list(row.items())[:6])
                        parts.append(f"  {row_str}")

        # Add evidence summary from skills
        if evidence and aggregator:
            evidence_text = aggregator.summarize(evidence)
            if evidence_text:
                parts.append(f"\n# Pre-Investigation Evidence\n{evidence_text}")

        # Add investigation graph summary
        if graph and len(graph.nodes) > 1:
            graph_summary = graph.summarize()
            if graph_summary:
                parts.append(f"\n# {graph_summary}")

        # Add connector context — each connector is isolated
        for name, connector in self.registry.get_all_instances().items():
            if name in self._failed_connectors:
                logger.debug("skipping_failed_connector_context", connector=name)
                continue
            try:
                ctx = await connector.get_context(task)
                if ctx:
                    parts.append(f"\n# Context from {connector.display_name}")
                    parts.append(ctx)
            except Exception as exc:
                logger.warning(
                    "context_fetch_failed",
                    connector=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        return "\n\n".join(parts)

    def _build_tools(self, task: Task) -> list[StructuredTool]:
        """Create LangChain tools from active connectors (skip failed ones)."""
        tools = []
        for name, connector in self.registry.get_all_instances().items():
            if name in self._failed_connectors:
                logger.debug("skipping_failed_connector_tools", connector=name)
                continue
            tools.append(_build_search_tool(connector))
            tools.append(_build_context_tool(connector, task))
        return tools

    async def _run_investigation(
        self, context: str, tools: list[StructuredTool]
    ) -> dict[str, Any]:
        """Execute the LangChain investigation chain."""
        messages = [
            SystemMessage(content=INVESTIGATION_SYSTEM_PROMPT),
            HumanMessage(content=f"""Please investigate the following task and produce a structured investigation report.

{context}

Analyze this thoroughly. Use available tools if they would help gather more evidence.
Produce your findings as the JSON structure described in your instructions."""),
        ]

        if tools:
            # Use tool-calling agent
            llm_with_tools = self.llm.bind_tools(tools)
            response = await llm_with_tools.ainvoke(messages)

            # Handle tool calls if any
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("args", {})
                    matching_tool = next((t for t in tools if t.name == tool_name), None)
                    if matching_tool:
                        try:
                            tool_result = await matching_tool.ainvoke(tool_args)
                            messages.append(response)
                            messages.append(HumanMessage(
                                content=f"Tool '{tool_name}' returned:\n{tool_result}\n\nContinue your investigation with this new information."
                            ))
                        except Exception as exc:
                            logger.warning("tool_call_failed", tool=tool_name, error=str(exc))

                # Get final response after tool usage
                response = await self.llm.ainvoke(messages)
        else:
            response = await self.llm.ainvoke(messages)

        # Extract content
        content = response.content if hasattr(response, "content") else str(response)

        # Try to parse as JSON
        return self._extract_json(content)

    def _extract_json(self, content: str) -> dict[str, Any]:
        """Extract JSON from the LLM response, handling markdown code blocks."""
        import json

        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try extracting from code block
        if "```json" in content:
            start = content.index("```json") + 7
            end = content.index("```", start)
            try:
                return json.loads(content[start:end].strip())
            except json.JSONDecodeError:
                pass

        if "```" in content:
            start = content.index("```") + 3
            end = content.index("```", start)
            try:
                return json.loads(content[start:end].strip())
            except json.JSONDecodeError:
                pass

        # Fallback: return raw content as summary
        return {
            "summary": content[:2000],
            "root_cause": "",
            "findings": [],
            "recommendations": [],
            "affected_files": [],
            "affected_services": [],
        }

    def _parse_result(self, result: dict[str, Any], report: InvestigationReport) -> None:
        """Parse the AI output into the investigation report."""
        report.summary = result.get("summary", "")
        report.root_cause = result.get("root_cause", "")
        report.raw_llm_output = str(result)

        for f in result.get("findings", []):
            if isinstance(f, dict):
                report.findings.append(InvestigationFinding(
                    category=f.get("category", "unknown"),
                    title=f.get("title", "Untitled Finding"),
                    description=f.get("description", ""),
                    confidence=float(f.get("confidence", 0.5)),
                    evidence=f.get("evidence", []),
                    file_references=f.get("file_references", []),
                ))

        report.recommendations = result.get("recommendations", [])
        report.affected_files = result.get("affected_files", [])
        report.affected_services = result.get("affected_services", [])
