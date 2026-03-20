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
from pathlib import Path
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
from task_analyzer.investigation.task_classifier import TaskClassifier, TaskClassification
from task_analyzer.investigation.code_flow_engine import CodeFlowAnalysisEngine, LayerMap
from task_analyzer.investigation.sql_intelligence import SQLIntelligence, SQLIntelligenceResult
from task_analyzer.investigation.parallel_engine import ParallelAnalysisEngine, AnalysisResult
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
from task_analyzer.skills.code_analysis import CodeAnalysisSkill

logger = structlog.get_logger(__name__)


# ─── System Prompt ────────────────────────────────────────────────────────────

INVESTIGATION_SYSTEM_PROMPT = """\
You are an expert software engineer performing a structured investigation.
Your job is to analyze a task and produce an EVIDENCE-BASED investigation report.

## CRITICAL RULES -- READ CAREFULLY

1. **NEVER fabricate evidence.** Only cite evidence that is explicitly provided below.
   If you did not receive code, logs, or data for something, do NOT claim you found it.

2. **NEVER invent root causes.** If the evidence is insufficient to determine the
   root cause, you MUST say: "Insufficient evidence to determine root cause."
   Do NOT guess or speculate and present it as a finding.

3. **Confidence scores MUST reflect evidence quality:**
   - 0.9-1.0: You found the EXACT code line or configuration causing the bug
   - 0.7-0.8: You found the relevant code file and function but not the exact line
   - 0.5-0.6: You have database/log evidence suggesting a pattern but no code proof
   - 0.3-0.4: You have architectural knowledge suggesting a likely area but no direct evidence
   - 0.1-0.2: Pure speculation based on the task description alone
   NEVER assign confidence above 0.5 without code-level evidence (file path + code snippet).

4. **Separate VERIFIED findings from HYPOTHESES:**
   - category "verified_finding": backed by code, logs, or data you can cite
   - category "hypothesis": your best guess based on architecture knowledge
   - category "insufficient_evidence": areas where more investigation is needed

5. **SQL data is OBSERVATIONAL, not CAUSAL.** Database rows show current state,
   not why something happened. Do NOT claim a SQL row "proves" a root cause.
   SQL data can support a hypothesis but cannot confirm it without code evidence.

6. **The ticket description may be inaccurate.** The reporter describes symptoms,
   not causes. Do NOT assume the reporter's theory is correct.

## EVIDENCE QUALITY GUIDE

The evidence provided below is tagged with its source:
- [CODE] = from repository source code (highest reliability)
- [SQL] = from database query results (observational only)
- [SCHEMA] = from database schema discovery (structural only)
- [TICKET] = from the ticket system (reporter's perspective, may be inaccurate)
- [ARCHITECTURE] = from system configuration (infrastructure context)

Base your confidence scores on the HIGHEST quality evidence you have for each finding.

## WHAT TO DO WHEN EVIDENCE IS INSUFFICIENT

If you cannot find the root cause from the provided evidence:
1. State clearly what evidence is missing
2. List specific files, logs, or data that would be needed
3. Provide hypotheses clearly labeled as speculation
4. Recommend specific next investigation steps

## OUTPUT FORMAT

```json
{{
    "summary": "2-3 sentence summary. State clearly if evidence was sufficient or not.",
    "root_cause": "Root cause if evidence supports it. Otherwise: 'Insufficient evidence. Hypotheses: ...'",
    "evidence_quality": "sufficient|partial|insufficient",
    "findings": [
        {{
            "category": "verified_finding|hypothesis|insufficient_evidence|related_code|configuration_issue",
            "title": "Short title",
            "description": "Description. For hypotheses, explain what evidence would confirm or deny this.",
            "confidence": 0.0-1.0,
            "evidence": ["[SOURCE] Evidence item with source tag"],
            "file_references": ["path/to/file.py:line_number"]
        }}
    ],
    "missing_evidence": [
        "Specific file or data that would help: e.g., 'DeleteTrip() method source code'",
        "Specific log that would help: e.g., 'OVC API call logs for BP tenant'"
    ],
    "recommendations": [
        "Actionable recommendation 1"
    ],
    "affected_files": ["ONLY list files that NEED CODE CHANGES to fix this issue. Do NOT list diagnostic scripts, JSON logs, config files, or files you merely read during investigation. List the actual source code files (controllers, services, repositories, models) where code modifications are required."],
    "affected_services": ["service-name"]
}}
```
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

        # Wrap LLM with resilience layer (retry, circuit breaker, cache)
        from task_analyzer.core.llm_resilience import ResilientLLM
        self._resilient_llm = ResilientLLM(
            self.llm,
            max_retries=3,
            base_delay=2.0,
            timeout=90.0,
            cache_ttl=3600.0,
            enable_cache=True,
        )

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

        # Initialize new analysis engines
        self.task_classifier = TaskClassifier()
        self.code_flow_engine = CodeFlowAnalysisEngine()
        self.sql_intelligence = SQLIntelligence()
        self._workspace_index = None  # Set during workspace loading

        # Initialize skill registry
        self.skill_registry = SkillRegistry()
        self.skill_registry.register(RepoAnalysisSkill())
        self.skill_registry.register(TicketContextSkill())
        self.skill_registry.register(LogAnalysisSkill())
        self.skill_registry.register(DatabaseAnalysisSkill())
        self.skill_registry.register(CrossRepoAnalysisSkill())
        self.skill_registry.register(DatabaseSchemaSkill())
        self.skill_registry.register(CodeAnalysisSkill())
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

                # Telemetry: log workspace architecture
                self.audit.log_workspace_loaded(
                    task_id="engine_init",
                    repositories=[r.get("name", "?") for r in workspace.repos],
                    dependencies=workspace.dependencies,
                    services=list(workspace.services.keys()),
                )

                # Telemetry: log dependency resolution
                for profile in self.profiles:
                    dep_names = workspace.get_dependencies(profile.repo_name)
                    if dep_names:
                        self.audit.log_dependency_resolution(
                            task_id="engine_init",
                            primary_repo=profile.repo_name,
                            loaded_repos=dep_names,
                            profiles_count=len(self.profiles),
                        )

                # Initialize workspace intelligence index
                try:
                    from task_analyzer.workspace_intelligence.index import WorkspaceIndex
                    self._workspace_index = WorkspaceIndex()

                    # Auto-index repos that haven't been indexed or are stale (>24h)
                    for repo_info in workspace.repos:
                        repo_name = repo_info.get("name", "")
                        repo_path = repo_info.get("path", "")
                        if not repo_name or not repo_path:
                            continue
                        age = self._workspace_index.get_repo_scan_age(repo_name)
                        if age is None or age > 86400:  # 24 hours
                            logger.info("indexing_repository", repo=repo_name)
                            self._workspace_index.index_repository(repo_name, repo_path)

                    # Build schema relations if SQL connector available
                    db_conn = None
                    for cn, co in registry.get_all_instances().items():
                        ct = getattr(co, "connector_type", None)
                        if ct and hasattr(ct, "value") and ct.value == "sql_database":
                            db_conn = co
                            break
                    if db_conn:
                        from task_analyzer.workspace_intelligence.schema_relation_builder import SchemaRelationBuilder
                        builder = SchemaRelationBuilder()
                        # Use first tenant DB from system map if available
                        try:
                            from task_analyzer.investigation.planner import load_system_map
                            smap = load_system_map()
                            tenant_names = smap.get_all_tenant_names()
                            if tenant_names:
                                tenant_db = smap.resolve_tenant_db(tenant_names[0])
                                fk_age = self._workspace_index._get_conn().execute(
                                    "SELECT COUNT(*) FROM db_foreign_keys"
                                ).fetchone()[0]
                                if fk_age == 0:
                                    builder.build(self._workspace_index, db_conn, tenant_db)
                        except Exception as fk_exc:
                            logger.debug("schema_relation_build_skipped", error=str(fk_exc)[:100])

                    idx_stats = self._workspace_index.get_stats()
                    logger.info("workspace_index_ready", **idx_stats)
                except Exception as idx_exc:
                    self._workspace_index = None
                    logger.debug("workspace_index_skipped", error=str(idx_exc)[:100])
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
            # Step 0: Classify the task
            classification = None
            try:
                classification = self.task_classifier.classify(
                    task.title, task.description,
                    task_type=task.task_type.value if task.task_type else "",
                )
                await _emit("classifying", f"Task classified: {classification.category} ({classification.investigation_strategy})")
                report.steps.append(InvestigationStep(
                    step_number=0,
                    action="Task classification",
                    reasoning=classification.summarize(),
                ))
                logger.info(
                    "task_classified",
                    task_id=task.id,
                    category=classification.category,
                    strategy=classification.investigation_strategy,
                    complexity=classification.complexity,
                )
                self.audit.log_task_classified(
                    task.id,
                    category=classification.category,
                    strategy=classification.investigation_strategy,
                    complexity=classification.complexity,
                    confidence=classification.confidence,
                    signals=classification.signals[:5],
                )
            except Exception as exc:
                logger.debug("classification_skipped", error=str(exc))

            # Step 0b: Run investigation planner
            investigation_plan = None
            try:
                from task_analyzer.investigation.planner import InvestigationPlanner

                # Find SQL connector for schema discovery
                db_connector_for_planner = None
                for cname, conn in self.registry.get_all_instances().items():
                    ct = getattr(conn, "connector_type", None)
                    if ct and hasattr(ct, "value") and ct.value == "sql_database":
                        db_connector_for_planner = conn
                        break

                planner = InvestigationPlanner(
                    workspace_index=getattr(self, '_workspace_index', None),
                )
                investigation_plan = planner.plan(
                    task.title, task.description,
                    db_connector=db_connector_for_planner,
                    classification=classification,
                )
                logger.info(
                    "investigation_plan_ready",
                    entities=investigation_plan.entities[:5],
                    systems=investigation_plan.systems,
                    repos=investigation_plan.repos,
                    tables=investigation_plan.tables,
                )

                # Telemetry: log ranked table selection
                table_ranks = getattr(investigation_plan, "table_ranks", {})
                if table_ranks:
                    self.audit.log_tables_ranked(
                        task.id,
                        tables=[{"table": t, "rank": table_ranks.get(t, 4)} for t in investigation_plan.tables[:12]],
                        total_schema_tables=len(self.schema_discovery_cache) if hasattr(self, "schema_discovery_cache") else 0,
                    )

                # Configure SQL intelligence with tenant DB
                if investigation_plan.tenant_db:
                    self.sql_intelligence = SQLIntelligence(tenant_db=investigation_plan.tenant_db)

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

            # Step 1b: Run parallel analysis (code flow + deep investigation + skills)
            await _emit("parallel_analysis", "Running multi-layer parallel analysis...")
            report.steps.append(InvestigationStep(
                step_number=1,
                action="Multi-layer parallel analysis",
                reasoning="Running code flow analysis, deep investigation, and skills concurrently",
            ))

            parallel = ParallelAnalysisEngine()
            parallel.set_progress_callback(progress_callback)

            # Get repo paths and entities for agents
            repo_paths = []
            for p in self.profiles:
                rp = getattr(p, "repo_path", None)
                if rp and Path(rp).exists():
                    repo_paths.append(Path(rp))

            entities = []
            if investigation_plan:
                entities = investigation_plan.entities
            elif classification:
                entities = classification.priority_entities

            connectors = self.registry.get_all_instances()

            # ── Agent-based orchestration ────────────────────────────────
            from task_analyzer.investigation.agents import (
                EntityExtractionAgent, RepoScanAgent, SchemaDiscoveryAgent,
                CodeFlowAgent, TicketContextAgent, CodeDeepDiveAgent,
                SQLIntelligenceAgent, EvidenceMergeAgent,
            )
            from task_analyzer.investigation.parallel_engine import AgentOrchestrator

            orchestrator = AgentOrchestrator(parallel)

            # Wave 1: Independent agents (no dependencies)
            orchestrator.register_agent(EntityExtractionAgent())
            if repo_paths and entities:
                orchestrator.register_agent(RepoScanAgent())
                orchestrator.register_agent(CodeFlowAgent())
            orchestrator.register_agent(SchemaDiscoveryAgent())
            orchestrator.register_agent(TicketContextAgent())

            # Wave 2: Dependent agents
            if repo_paths:
                orchestrator.register_agent(CodeDeepDiveAgent())
            orchestrator.register_agent(SQLIntelligenceAgent())

            # Wave 3: Final merge
            orchestrator.register_agent(EvidenceMergeAgent())

            # Also run remaining skills alongside agents
            available_skills = self.skill_registry.list_available(connectors)
            skill_context = {"profiles": self.profiles}
            if investigation_plan:
                skill_context["investigation_plan"] = investigation_plan

            suggested_skill_names = set()
            if investigation_plan and investigation_plan.skills:
                skill_name_map = {
                    "RepoAnalysisSkill": "repo_analysis",
                    "TicketContextSkill": "ticket_context",
                    "DatabaseAnalysisSkill": "database_analysis",
                    "DatabaseSchemaSkill": "database_schema",
                    "SQLQuerySkill": "sql_query",
                    "CrossRepoAnalysisSkill": "cross_repo_analysis",
                    "CodeAnalysisSkill": "code_analysis",
                    "LogAnalysisSkill": "log_analysis",
                }
                for s in investigation_plan.skills:
                    suggested_skill_names.add(skill_name_map.get(s, s))

            # Skills already handled by agents — skip duplicates
            agent_handled = {"ticket_context", "database_schema", "sql_query", "code_analysis"}

            for skill in available_skills:
                if skill.name in agent_handled:
                    continue
                if suggested_skill_names and skill.name not in suggested_skill_names:
                    continue
                async def _run_skill(s=skill):
                    return await s.run(task, skill_context, self.guard, connectors, graph)
                parallel.add_task(f"skill_{skill.name}", _run_skill, timeout=60, priority=5)

            # Build agent tasks and execute all in parallel waves
            common_kwargs = {
                "task_title": task.title,
                "task_description": task.description or "",
                "entities": entities,
                "profiles": self.profiles,
                "connectors": connectors,
                "plan": investigation_plan,
                "classification": classification,
                "progress_callback": progress_callback,
                "_task_obj": task,
            }
            orchestrator.build_tasks(**common_kwargs)

            analysis_result = await parallel.execute()

            # Extract results from agent context
            merged = orchestrator.context.merged_evidence
            if merged:
                deep_evidence = {
                    "code_files": merged.code_files,
                    "code_flows": merged.code_flows,
                    "sql_tables": merged.sql_tables,
                    "sql_schema": merged.sql_schema,
                    "repo_search_results": merged.repo_search_results,
                    "entities": merged.entities,
                    "quality": merged.quality,
                    "loops_completed": merged.loops_completed,
                    "tenant_db": merged.tenant_db,
                }
                layer_map = merged.layer_map
                sql_results = merged.query_results
            else:
                deep_evidence = analysis_result.deep_evidence
                layer_map = analysis_result.layer_map
                sql_results = []

            skill_results = analysis_result.skill_results

            # Log parallel execution metrics
            metrics = analysis_result.export_metrics()
            logger.info(
                "parallel_analysis_complete",
                task_id=task.id,
                total_ms=metrics["total_duration_ms"],
                completed=metrics["completed"],
                failed=metrics["failed"],
                has_layer_map=metrics["has_layer_map"],
                has_deep_evidence=metrics["has_deep_evidence"],
            )

            report.steps.append(InvestigationStep(
                step_number=2,
                action="Parallel analysis completed",
                reasoning=(
                    f"Completed {metrics['completed']} tasks, "
                    f"{metrics['failed']} failed, "
                    f"total {metrics['total_duration_ms']}ms"
                ),
            ))

            # SQL Intelligence and code-table queries are now handled by
            # SQLIntelligenceAgent and CodeDeepDiveAgent in the parallel waves.
            # sql_results is already populated from the agent context above.

            # Step 3: Aggregate evidence and rank root causes
            await _emit("evidence_aggregation", "Aggregating evidence...")
            report.steps.append(InvestigationStep(
                step_number=3,
                action="Aggregating evidence and ranking root causes",
                reasoning="Consolidating skill findings into unified evidence structure",
            ))

            # Collect skill errors from parallel execution
            skill_errors: list[str] = []
            for task_name, task_result in analysis_result.task_results.items():
                if task_result.status in ("failed", "timeout"):
                    skill_errors.append(f"{task_name}: {task_result.error or task_result.status}")

            aggregator = EvidenceAggregator()
            evidence = aggregator.aggregate(skill_results)

            root_engine = RootCauseEngine()
            hypotheses = root_engine.analyze(graph.export(), evidence)

            # Step 3b: Build evidence graph
            await _emit("building_graph", "Building evidence graph...")
            graph_builder = GraphBuilder()
            graph_builder.build(graph, task.id, evidence, hypotheses)

            # Add layer map nodes to graph
            if layer_map:
                for node in layer_map.nodes.values():
                    graph.add_node(
                        f"code:{node.name}",
                        NodeType.FILE,
                        {"label": node.name, "layer": node.layer, "file": node.file_path},
                    )

            # Step 4: Build context (with connector error isolation)
            await _emit("building_context", "Building investigation context...")
            report.steps.append(InvestigationStep(
                step_number=4,
                action="Building investigation context",
                reasoning="Gathering task details, project knowledge, skill findings, and connector context",
            ))
            context = await self._build_context(
                task, evidence, aggregator, graph, skill_results, investigation_plan,
                deep_evidence=deep_evidence,
                layer_map=layer_map,
                classification=classification,
                sql_results=sql_results,
            )

            # Step 5: Skip tool building — PDI AI Gateway rejects tool calling
            tools: list = []
            report.steps.append(InvestigationStep(
                step_number=5,
                action="Preparing investigation",
                reasoning="Direct analysis mode (tool calling disabled for gateway compatibility)",
            ))

            # Step 6: Run the AI investigation
            # PDI AI Gateway does not support tool calling — send without tools
            # to avoid the retry penalty on every investigation
            await _emit("ai_reasoning", "Running AI reasoning with Claude...")
            report.steps.append(InvestigationStep(
                step_number=6,
                action="Running AI analysis",
                tool_used="Claude via LangChain",
                reasoning="Sending context, evidence, and task to Claude for multi-step reasoning",
            ))

            llm_succeeded = False
            try:
                result = await self._run_investigation(context, tools)
                self._parse_result(result, report)
                llm_succeeded = True

                # Log resilience metrics
                metrics = self._resilient_llm.get_metrics()
                report.steps.append(InvestigationStep(
                    step_number=6,
                    action="AI analysis completed",
                    reasoning=f"LLM calls: {metrics['total_calls']}, retries: {metrics['retried']}, cache hits: {metrics['cache_hits']}",
                ))
            except Exception as llm_exc:
                llm_error_msg = str(llm_exc)
                llm_error_type = type(llm_exc).__name__

                logger.error(
                    "llm_call_failed",
                    task_id=task.id,
                    error=llm_error_msg[:500],
                    error_type=llm_error_type,
                    resilience_metrics=self._resilient_llm.get_metrics(),
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
        deep_evidence: dict | None = None,
        layer_map: LayerMap | None = None,
        classification: TaskClassification | None = None,
        sql_results: list | None = None,
    ) -> str:
        """Assemble the full context with evidence quality tags."""
        parts = [
            "# Task Under Investigation",
            "[TICKET] " + task.full_context,
        ]

        # ── [WORKSPACE_ARCHITECTURE] — workspace topology for Claude ─────
        ws_arch = self._build_workspace_architecture(investigation_plan)
        if ws_arch:
            parts.append(f"\n# [WORKSPACE_ARCHITECTURE]\n{ws_arch}")

        # ── [CODE] Workspace Index — entity-matched classes, routes, deps ──
        idx_context = self._build_workspace_index_context(investigation_plan, classification)
        if idx_context:
            parts.append(f"\n# [CODE] Workspace Index Matches\n{idx_context}")

        # Task classification context
        if classification:
            parts.append(f"\n# Task Classification")
            parts.append(f"Category: {classification.category} | Strategy: {classification.investigation_strategy}")
            parts.append(f"Complexity: {classification.complexity} | Focus: {', '.join(classification.focus_areas[:5])}")
            if classification.signals:
                parts.append(f"Signals: {', '.join(classification.signals[:5])}")

        # Deep investigation evidence (highest priority -- most comprehensive)
        if deep_evidence:
            from task_analyzer.investigation.deep_investigator import DeepInvestigator
            di = DeepInvestigator([], {})
            di.evidence = deep_evidence
            deep_context = di.build_context_for_llm()
            if deep_context:
                parts.append(f"\n# Deep Investigation Evidence\n{deep_context}")

        # Code flow layer map (cross-layer execution paths)
        if layer_map and layer_map.nodes:
            parts.append(f"\n# [CODE] Code Flow Analysis")
            parts.append(layer_map.summarize())

            # Add detailed flow traces
            for flow in layer_map.flows[:8]:
                chain_parts = []
                if flow.entry_point:
                    chain_parts.append(f"Controller: {flow.entry_point}")
                if flow.service:
                    chain_parts.append(f"Service: {flow.service}")
                if flow.repository:
                    chain_parts.append(f"Repository: {flow.repository}")
                if flow.db_tables:
                    chain_parts.append(f"Tables: {', '.join(flow.db_tables[:3])}")
                parts.append(f"  Flow: {' -> '.join(chain_parts)} (confidence: {flow.confidence:.0%})")

            # Add code snippets from key nodes
            for node in list(layer_map.nodes.values())[:10]:
                if node.content_snippet:
                    parts.append(f"\n### {node.layer}: {node.name} ({node.file_path})")
                    parts.append(f"```\n{node.content_snippet[:400]}\n```")

        # SQL Intelligence results
        if sql_results:
            parts.append(f"\n# [SQL] SQL Intelligence Results ({len(sql_results)} queries)")
            parts.append("NOTE: SQL data shows current state only. It does NOT prove causation.")
            for qr in sql_results[:8]:
                parts.append(f"\n### {qr.get('table', '?')} ({qr.get('row_count', 0)} rows) - {qr.get('description', '')}")
                parts.append(f"Insight: {qr.get('expected_insight', '')}")
                cols = qr.get("columns", [])
                if cols:
                    parts.append(f"Columns: {', '.join(cols[:15])}")
                for row in qr.get("sample_rows", [])[:3]:
                    row_str = " | ".join(f"{k}={v[:50]}" for k, v in list(row.items())[:6])
                    parts.append(f"  {row_str}")

        # Evidence quality summary -- tell Claude what it has and doesn't have
        quality = self._build_evidence_quality_summary(
            evidence, skill_results, deep_evidence,
            layer_map=layer_map, classification=classification,
            sql_results=sql_results,
        )
        parts.append(f"\n# Evidence Quality Assessment\n{quality}")

        # Add system architecture from system_map.json
        try:
            from task_analyzer.investigation.planner import load_system_map
            system_map = load_system_map()
            if system_map.services:
                parts.append(f"\n# [ARCHITECTURE] {system_map.summarize()}")
        except Exception:
            pass

        # Add investigation plan
        if investigation_plan and hasattr(investigation_plan, "summarize"):
            plan_summary = investigation_plan.summarize()
            if plan_summary:
                parts.append(f"\n# [ARCHITECTURE] {plan_summary}")

        # Add workspace architecture (cross-repo awareness)
        if self._workspace_summary:
            parts.append(f"\n# [ARCHITECTURE] {self._workspace_summary}")

        # Add project profiles (including dependent repos)
        if self.profiles:
            parts.append("\n# [CODE] Project Knowledge")
            for profile in self.profiles:
                parts.append(profile.context_summary)

        # Add database schema from skill results
        if skill_results and "database_schema" in skill_results:
            schema = skill_results["database_schema"]
            schema_summary = schema.get("schema_summary", "")
            if schema_summary:
                parts.append(f"\n# [SCHEMA] Database Schema\n{schema_summary}")

        # Add cross-repo analysis from skill results
        if skill_results and "cross_repo_analysis" in skill_results:
            cross = skill_results["cross_repo_analysis"]
            if cross.get("related_services"):
                parts.append("\n# [ARCHITECTURE] Related Services")
                for svc in cross["related_services"]:
                    parts.append(f"- **{svc['name']}** in {svc.get('repo', '?')} (`{svc.get('path', '?')}`)")

        # Add code analysis results (code flows and table references)
        if skill_results and "code_analysis" in skill_results:
            code_data = skill_results["code_analysis"]
            code_tables = code_data.get("code_tables", [])
            code_flows = code_data.get("code_flows", [])
            code_refs = code_data.get("code_references", [])

            if code_tables or code_flows or code_refs:
                parts.append("\n# [CODE] Code Analysis")

            if code_tables:
                parts.append(f"\n### Tables Referenced in Code ({len(code_tables)})")
                for t in code_tables[:15]:
                    parts.append(f"- {t}")

            if code_flows:
                parts.append(f"\n### Application Layers Detected")
                seen_flows = set()
                for flow in code_flows[:15]:
                    key = f"{flow['layer']}: {flow['class']}"
                    if key not in seen_flows:
                        parts.append(f"- {flow['layer']}: **{flow['class']}** ({flow['file']})")
                        seen_flows.add(key)

            if code_refs:
                parts.append(f"\n### Code References ({len(code_refs)})")
                for ref in code_refs[:10]:
                    parts.append(f"- `{ref['file']}:{ref['line']}` {ref['context'][:80]}")

        # Add SQL query results from SQLQuerySkill
        if skill_results and "sql_query" in skill_results:
            sql_data = skill_results["sql_query"]
            query_results = sql_data.get("query_results", [])
            if query_results:
                parts.append("\n# [SQL] Database Query Results")
                parts.append("NOTE: SQL data shows current state only. It does NOT prove causation.")
                parts.append("Do NOT claim SQL rows 'prove' a root cause without code-level evidence.")
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

    def _build_workspace_architecture(self, investigation_plan: Any | None = None) -> str:
        """
        Build the [WORKSPACE_ARCHITECTURE] context section.

        Summarizes:
          - Repositories in the workspace
          - Repository dependency graph
          - Known services from workspace_profile.json
          - Database topology from system_map.json
          - Workspace index statistics (if available)
        """
        sections = []

        # Repositories
        try:
            from task_analyzer.knowledge.workspace_scanner import load_workspace_profile
            workspace = load_workspace_profile()

            if workspace.repos:
                sections.append("## Repositories")
                for r in workspace.repos:
                    sections.append(f"- {r.get('name', '?')} (`{r.get('path', '?')}`)")

                # Dependency graph
                if workspace.dependencies:
                    sections.append("\n## Dependency Graph")
                    for repo, deps in workspace.dependencies.items():
                        for dep in deps:
                            sections.append(f"- {repo} -> {dep}")

                # Services
                if workspace.services:
                    sections.append("\n## Services")
                    for svc_name, svc_info in workspace.services.items():
                        repo = svc_info.get("repo", "?")
                        path = svc_info.get("path", "?")
                        sections.append(f"- {svc_name} located in {repo} repository (`{path}`)")
        except Exception:
            pass

        # Database topology from system_map
        try:
            from task_analyzer.investigation.planner import load_system_map
            smap = load_system_map()

            if smap.flows:
                sections.append("\n## Data Flows")
                for flow_name, systems in smap.flows.items():
                    sections.append(f"- {flow_name}: {' -> '.join(systems)}")

            if smap.tenant_db_map:
                sections.append("\n## Database Topology")
                for tenant, db in smap.tenant_db_map.items():
                    sections.append(f"- Tenant '{tenant}' -> database {db}")

            if investigation_plan and investigation_plan.tenant:
                sections.append(f"\n## Active Tenant: {investigation_plan.tenant} (database: {investigation_plan.tenant_db})")
        except Exception:
            pass

        # Workspace index statistics
        if self._workspace_index:
            try:
                stats = self._workspace_index.get_stats()
                sections.append("\n## Workspace Index")
                sections.append(
                    f"- {stats['repositories']} repositories indexed, "
                    f"{stats['classes']} classes, {stats['methods']} methods"
                )
                sections.append(
                    f"- {stats['api_routes']} API routes, "
                    f"{stats['class_table_refs']} code-to-table references"
                )
                sections.append(
                    f"- {stats['db_tables']} database tables, "
                    f"{stats['foreign_keys']} foreign key relationships"
                )
            except Exception:
                pass

        return "\n".join(sections) if sections else ""

    def _build_workspace_index_context(
        self, investigation_plan: Any | None = None,
        classification: TaskClassification | None = None,
    ) -> str:
        """
        Query the workspace index for entity-relevant code artifacts.

        Returns concrete [CODE] evidence: matching classes with their layers,
        file paths, API routes, dependencies, and table references.
        This gives Claude specific code locations instead of just architecture stats.
        """
        if not self._workspace_index:
            return ""

        sections = []
        entities = []
        if investigation_plan and investigation_plan.entities:
            entities = investigation_plan.entities[:15]
        elif classification and classification.priority_entities:
            entities = classification.priority_entities[:15]

        if not entities:
            return ""

        seen_classes = set()

        # Find classes matching entities — grouped by layer
        layer_groups: dict[str, list[dict]] = {}
        for entity in entities:
            if len(entity) < 3:
                continue
            classes = self._workspace_index.find_classes_by_entity(entity)
            for c in classes[:8]:
                key = f"{c['repo']}/{c['name']}"
                if key in seen_classes:
                    continue
                seen_classes.add(key)
                layer = c.get("layer", "unknown")
                if layer not in layer_groups:
                    layer_groups[layer] = []
                layer_groups[layer].append(c)

        # Format by layer (controllers first, then services, then repos, etc.)
        layer_order = [
            "api_controller", "service", "handler", "repository",
            "data_access", "model", "validator", "unknown",
        ]
        for layer in layer_order:
            group = layer_groups.get(layer, [])
            if not group:
                continue
            label = layer.replace("_", " ").title()
            sections.append(f"\n## {label}s ({len(group)})")
            for c in group[:10]:
                sections.append(
                    f"- **{c['name']}** in {c['repo']} (`{c['file_path']}`)"
                )
                # Add dependencies for this class
                deps = self._workspace_index.find_class_dependencies(c["name"])
                if deps:
                    sections.append(f"  Dependencies: {', '.join(deps[:5])}")
                # Add table references for this class
                tables = self._workspace_index.find_tables_referenced_by_class(c["name"])
                if tables:
                    sections.append(f"  DB Tables: {', '.join(tables[:5])}")
                # Read key code snippet from the actual source file
                snippet = self._read_class_snippet(c)
                if snippet:
                    sections.append(f"  ```\n{snippet}\n  ```")

        # Find relevant API routes
        route_sections = []
        for entity in entities[:8]:
            if len(entity) < 3:
                continue
            routes = self._workspace_index.find_api_routes(entity)
            for r in routes[:5]:
                route_line = f"- {r['http_method']} `{r['route_path']}` -> {r['class_name']} (`{r['file_path']}`)"
                if route_line not in route_sections:
                    route_sections.append(route_line)

        if route_sections:
            sections.append(f"\n## API Routes ({len(route_sections)})")
            sections.extend(route_sections[:15])

        if sections:
            sections.insert(0,
                "The following code artifacts were found in the persistent workspace index "
                "and are relevant to the entities extracted from the task:"
            )

        return "\n".join(sections) if sections else ""

    def _read_class_snippet(self, class_info: dict) -> str:
        """Read a code snippet from the source file around the class declaration."""
        try:
            # Resolve full path from workspace index
            conn = self._workspace_index._get_conn()
            row = conn.execute(
                "SELECT r.path FROM repositories r JOIN code_classes c ON c.repo_id = r.id "
                "WHERE c.name = ? AND c.file_path = ? LIMIT 1",
                (class_info["name"], class_info["file_path"]),
            ).fetchone()
            if not row:
                return ""

            full_path = Path(row["path"]) / class_info["file_path"]
            if not full_path.exists():
                return ""

            content = full_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")
            line_num = class_info.get("line_number", 0) or 0

            # Extract ~40 lines around the class declaration
            start = max(0, line_num - 3)
            end = min(len(lines), start + 40)
            snippet = "\n".join(lines[start:end])

            # Truncate if too long
            if len(snippet) > 1500:
                snippet = snippet[:1500] + "\n  // ... truncated"

            return snippet
        except Exception:
            return ""

    def _build_evidence_quality_summary(
        self, evidence: dict | None, skill_results: dict | None,
        deep_evidence: dict | None = None,
        layer_map: LayerMap | None = None,
        classification: TaskClassification | None = None,
        sql_results: list | None = None,
    ) -> str:
        """
        Build a clear summary of what evidence is available and what is missing.
        This helps Claude calibrate its confidence appropriately.
        """
        lines = []

        # Check deep investigation evidence first (most comprehensive)
        deep_code = 0
        deep_sql = 0
        deep_flows = 0
        deep_refs = 0
        if deep_evidence:
            deep_code = len(deep_evidence.get("code_files", []))
            deep_sql = len([t for t in deep_evidence.get("sql_tables", []) if (t.get("row_count") or 0) > 0])
            deep_flows = len(deep_evidence.get("code_flows", []))
            deep_refs = len(deep_evidence.get("repo_search_results", []))
            dq = deep_evidence.get("quality", {})
            lines.append(f"DEEP INVESTIGATION: {dq.get('level', '?')} ({dq.get('score', 0)}/100)")
            for d in dq.get("details", []):
                lines.append(f"  - {d}")
            lines.append("")

        # Check code evidence (from skills + deep)
        has_code_files = deep_code > 0
        has_code_flows = deep_flows > 0
        has_code_refs = deep_refs > 0
        if skill_results and "code_analysis" in skill_results:
            cd = skill_results["code_analysis"]
            has_code_files = has_code_files or bool(cd.get("relevant_files"))
            has_code_flows = has_code_flows or bool(cd.get("code_flows"))
            has_code_refs = has_code_refs or bool(cd.get("code_references"))

        if has_code_refs:
            lines.append("[CODE] Source code references: AVAILABLE -- you may cite specific files and lines")
        else:
            lines.append("[CODE] Source code references: NOT AVAILABLE -- no relevant code files were found")
            lines.append("  -> Without code evidence, confidence for root cause should be below 0.5")

        if has_code_flows:
            lines.append("[CODE] Application code flows: AVAILABLE")
        else:
            lines.append("[CODE] Application code flows: NOT AVAILABLE -- no Controller/Service/Repository patterns found")

        # Check SQL evidence (from skills + deep)
        has_sql = deep_sql > 0
        sql_tables = deep_sql
        if skill_results and "sql_query" in skill_results:
            sq = skill_results["sql_query"]
            sql_tables = len(sq.get("tables_queried", []))
            has_sql = sql_tables > 0

        if has_sql:
            lines.append(f"[SQL] Database query results: AVAILABLE ({sql_tables} tables queried)")
            lines.append("  -> SQL data is OBSERVATIONAL. It shows state, not cause.")
        else:
            lines.append("[SQL] Database query results: NOT AVAILABLE")

        # Check schema
        has_schema = False
        if skill_results and "database_schema" in skill_results:
            has_schema = bool(skill_results["database_schema"].get("tables"))

        if has_schema:
            lines.append("[SCHEMA] Database schema: AVAILABLE")
        else:
            lines.append("[SCHEMA] Database schema: NOT AVAILABLE")

        # Check repo evidence
        has_files = bool(evidence and evidence.get("files"))
        has_commits = bool(evidence and evidence.get("commits"))
        has_errors = bool(evidence and evidence.get("errors"))

        if has_files:
            lines.append(f"[CODE] Repository files: {len(evidence['files'])} relevant files found")
        if has_commits:
            lines.append(f"[CODE] Recent commits: {len(evidence['commits'])} commits found")
        if has_errors:
            lines.append(f"[SQL] Error patterns: {len(evidence['errors'])} errors found")

        # Check layer map (code flow analysis)
        has_layer_map = layer_map is not None and len(layer_map.nodes) > 0
        if has_layer_map:
            stats = layer_map.export()["stats"]
            lines.append(
                f"[CODE] Code flow analysis: AVAILABLE "
                f"({stats['controllers']} controllers, {stats['services']} services, "
                f"{stats['repositories']} repositories, {stats['flows_traced']} execution flows)"
            )
            has_code_flows = True  # Override if layer map found flows
        else:
            lines.append("[CODE] Code flow analysis: NOT AVAILABLE")

        # Check SQL intelligence results
        if sql_results:
            lines.append(f"[SQL] SQL Intelligence: {len(sql_results)} targeted queries executed")
            has_sql = True
        else:
            lines.append("[SQL] SQL Intelligence: NOT AVAILABLE")

        # Task classification context
        if classification:
            lines.append(f"[CLASSIFICATION] Task type: {classification.category} ({classification.investigation_strategy})")

        # Overall assessment
        lines.append("")
        if has_code_refs and has_sql and has_layer_map:
            lines.append("OVERALL: COMPREHENSIVE evidence available (code + flows + SQL). High confidence findings possible.")
        elif has_code_refs and has_sql:
            lines.append("OVERALL: Code + SQL evidence available. Moderate-to-high confidence findings possible.")
        elif has_code_refs and has_layer_map:
            lines.append("OVERALL: Code + flow analysis available. Code-based findings with flow context possible.")
        elif has_code_refs:
            lines.append("OVERALL: Code evidence available but no SQL data. Code-based findings possible.")
        elif has_sql:
            lines.append("OVERALL: SQL data available but NO code evidence. Findings should be hypotheses only (confidence < 0.5).")
        else:
            lines.append("OVERALL: INSUFFICIENT EVIDENCE. No code or SQL data found. Report should state this clearly.")

        return "\n".join(lines)

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
        """Execute the LangChain investigation chain with resilience."""
        messages = [
            SystemMessage(content=INVESTIGATION_SYSTEM_PROMPT),
            HumanMessage(content=f"""Please investigate the following task and produce a structured investigation report.

{context}

Analyze this thoroughly. Use available tools if they would help gather more evidence.
Produce your findings as the JSON structure described in your instructions."""),
        ]

        # Use resilient LLM wrapper (retry + circuit breaker + cache)
        content = await self._resilient_llm.invoke(messages)

        # Try to parse as JSON
        return self._extract_json(content)

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

        # Add missing evidence as recommendations if present
        missing = result.get("missing_evidence", [])
        if missing:
            report.recommendations.extend(
                [f"Investigate: {m}" for m in missing[:5]]
            )

        report.affected_files = result.get("affected_files", [])
        report.affected_services = result.get("affected_services", [])
