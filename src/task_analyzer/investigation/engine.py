"""
Investigation Engine — LangChain-powered AI reasoning for task investigation.

This is the core of TraceAI. It orchestrates:

  1. Task ingestion from the configured ticket source
  2. Repository context building from the project knowledge profile
  3. Skill execution (repo analysis, ticket context, log analysis, DB analysis)
  4. Evidence aggregation and root cause ranking
  5. Multi-step AI reasoning using Claude via LangChain
  6. Structured investigation report generation

Security: Every operation goes through:
  1. SecurityGuard.validate_tool() — tool permission check
  2. RateLimiter.acquire() — rate limit enforcement
  3. AuditLogger.log_tool_call() — audit trail
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.base.registry import ConnectorRegistry
from task_analyzer.core.audit_logger import AuditLogger
from task_analyzer.core.rate_limiter import RateLimiter
from task_analyzer.core.security_guard import SecurityGuard
from task_analyzer.investigation.evidence_aggregator import EvidenceAggregator
from task_analyzer.investigation.graph_engine import InvestigationGraph
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

        # Initialize Claude via LangChain
        self.llm = ChatAnthropic(
            model=config.llm_model,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
        )

        # Security, rate limiting, and audit logging
        self.guard = SecurityGuard(safe_mode=(config.mode == "safe"))
        self.rate_limiter = RateLimiter()
        self.audit = AuditLogger()

        # Attach rate limiter to all connectors
        for name, connector in registry.get_all_instances().items():
            connector.set_rate_limiter(self.rate_limiter)

        # Initialize skill registry
        self.skill_registry = SkillRegistry()
        self.skill_registry.register(RepoAnalysisSkill())
        self.skill_registry.register(TicketContextSkill())
        self.skill_registry.register(LogAnalysisSkill())
        self.skill_registry.register(DatabaseAnalysisSkill())

    async def investigate(self, task: Task) -> InvestigationReport:
        """
        Run a full investigation on a task.

        Steps:
          1. Initialize investigation graph
          2. Run available skills (repo, ticket, log, database)
          3. Aggregate evidence and rank root causes
          4. Build context (task + profile + skill findings + connector context)
          5. Create tools from active connectors
          6. Run the LangChain agent
          7. Parse and return the structured report
        """
        report = InvestigationReport(
            task_id=task.id,
            task_title=task.title,
            status=InvestigationStatus.IN_PROGRESS,
            model_used=self.config.llm_model,
        )

        self.audit.log_investigation_start(task.id, task.title)

        try:
            start_time = time.time()

            # Step 1: Initialize investigation graph
            graph = InvestigationGraph()
            graph.add_node(task.id, "ticket", {
                "title": task.title,
                "type": task.task_type.value,
            })

            # Step 2: Run available skills
            report.steps.append(InvestigationStep(
                step_number=1,
                action="Running investigation skills",
                reasoning="Executing available skills to gather evidence before AI reasoning",
            ))

            connectors = self.registry.get_all_instances()
            available_skills = self.skill_registry.list_available(connectors)
            skill_results: dict[str, Any] = {}

            skill_context = {"profiles": self.profiles}

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
                except asyncio.TimeoutError:
                    skill_elapsed = int((time.time() - skill_start) * 1000)
                    self.audit.log_skill_execution(
                        task.id, skill.name, "timeout",
                        duration_ms=skill_elapsed,
                    )
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
                    logger.warning(
                        "skill_execution_failed",
                        skill=skill.name,
                        task_id=task.id,
                        error=str(exc),
                    )

            # Step 3: Aggregate evidence and rank root causes
            report.steps.append(InvestigationStep(
                step_number=2,
                action="Aggregating evidence and ranking root causes",
                reasoning="Consolidating skill findings into unified evidence structure",
            ))

            aggregator = EvidenceAggregator()
            evidence = aggregator.aggregate(skill_results)

            root_engine = RootCauseEngine()
            hypotheses = root_engine.analyze(graph.export(), evidence)

            # Step 4: Build context
            report.steps.append(InvestigationStep(
                step_number=3,
                action="Building investigation context",
                reasoning="Gathering task details, project knowledge, skill findings, and connector context",
            ))
            context = await self._build_context(task, evidence, aggregator)

            # Step 5: Create tools
            tools = self._build_tools(task)
            tool_names = [t.name for t in tools]
            report.steps.append(InvestigationStep(
                step_number=4,
                action="Preparing investigation tools",
                reasoning=f"Available tools: {', '.join(tool_names) if tool_names else 'None'}",
            ))

            # Step 6: Run the AI investigation
            report.steps.append(InvestigationStep(
                step_number=5,
                action="Running AI analysis",
                tool_used="Claude via LangChain",
                reasoning="Sending context, evidence, and task to Claude for multi-step reasoning",
            ))

            result = await self._run_investigation(context, tools)

            # Step 7: Parse results
            report.steps.append(InvestigationStep(
                step_number=6,
                action="Parsing investigation results",
                reasoning="Extracting structured findings from AI output",
            ))

            self._parse_result(result, report)

            # Attach graph, hypotheses, and evidence to report
            report.investigation_graph = graph.export()
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
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            report.status = InvestigationStatus.FAILED
            report.error = str(exc)
            report.completed_at = datetime.utcnow()
            self.audit.log_investigation_complete(
                task.id, "failed", 0,
                int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0,
            )
            logger.error("investigation_failed", task_id=task.id, error=str(exc))

        return report

    async def _build_context(
        self,
        task: Task,
        evidence: dict | None = None,
        aggregator: EvidenceAggregator | None = None,
    ) -> str:
        """Assemble the full context for the investigation."""
        parts = [
            "# Task Under Investigation",
            task.full_context,
        ]

        # Add project profiles
        if self.profiles:
            parts.append("\n# Project Knowledge")
            for profile in self.profiles:
                parts.append(profile.context_summary)

        # Add evidence summary from skills
        if evidence and aggregator:
            evidence_text = aggregator.summarize(evidence)
            if evidence_text:
                parts.append(f"\n# Pre-Investigation Evidence\n{evidence_text}")

        # Add connector context
        for name, connector in self.registry.get_all_instances().items():
            try:
                ctx = await connector.get_context(task)
                if ctx:
                    parts.append(f"\n# Context from {connector.display_name}")
                    parts.append(ctx)
            except Exception as exc:
                logger.warning("context_fetch_failed", connector=name, error=str(exc))

        return "\n\n".join(parts)

    def _build_tools(self, task: Task) -> list[StructuredTool]:
        """Create LangChain tools from active connectors."""
        tools = []
        for name, connector in self.registry.get_all_instances().items():
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
