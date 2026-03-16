"""
Task Classifier -- Intelligent task categorization for investigation strategy.

Analyzes task title and description to determine:
  - Task category (bug, feature, performance, security, data-issue, integration)
  - Sub-category for targeted investigation
  - Complexity estimate
  - Suggested skills and investigation strategy
  - Priority signals

This classifier drives the investigation engine's strategy selection:
  - Bug tasks → focus on code flow tracing, error patterns, recent commits
  - Feature tasks → focus on architecture, dependencies, implementation patterns
  - Performance tasks → focus on query analysis, bottleneck detection
  - Data tasks → focus on SQL intelligence, schema analysis, data integrity
  - Integration tasks → focus on cross-service flows, API contracts

The classifier uses NLP pattern matching (no ML model required) to extract
signals from task text. It is domain-agnostic and works across any project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Task Categories ──────────────────────────────────────────────────────────

class TaskCategory:
    """Task category constants."""
    BUG = "bug"
    FEATURE = "feature"
    PERFORMANCE = "performance"
    SECURITY = "security"
    DATA_ISSUE = "data_issue"
    INTEGRATION = "integration"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class InvestigationStrategy:
    """Investigation strategy constants."""
    CODE_TRACE = "code_trace"           # Trace code flow from entry to DB
    ERROR_HUNT = "error_hunt"           # Find error patterns and root cause
    DATA_ANALYSIS = "data_analysis"     # Analyze data integrity and queries
    ARCHITECTURE = "architecture"       # Map service dependencies and flows
    PERFORMANCE = "performance"         # Profile queries and bottlenecks
    SECURITY_AUDIT = "security_audit"   # Check auth, permissions, injection
    FULL_STACK = "full_stack"           # Comprehensive multi-layer analysis


# ── Classification Result ────────────────────────────────────────────────────

@dataclass
class TaskClassification:
    """Result of task classification."""
    category: str = TaskCategory.UNKNOWN
    sub_category: str = ""
    complexity: str = "medium"          # low, medium, high
    confidence: float = 0.5
    signals: list[str] = field(default_factory=list)
    suggested_skills: list[str] = field(default_factory=list)
    investigation_strategy: str = InvestigationStrategy.FULL_STACK
    focus_areas: list[str] = field(default_factory=list)
    priority_entities: list[str] = field(default_factory=list)

    def summarize(self) -> str:
        """Human-readable summary."""
        parts = [
            f"Category: {self.category}",
            f"Sub-category: {self.sub_category}" if self.sub_category else "",
            f"Strategy: {self.investigation_strategy}",
            f"Complexity: {self.complexity}",
            f"Confidence: {self.confidence:.0%}",
        ]
        if self.focus_areas:
            parts.append(f"Focus: {', '.join(self.focus_areas)}")
        if self.signals:
            parts.append(f"Signals: {', '.join(self.signals[:5])}")
        return " | ".join(p for p in parts if p)


# ── Pattern Definitions ──────────────────────────────────────────────────────

# Each pattern set maps regex patterns to (category, sub_category, weight)
BUG_PATTERNS = [
    (r"\b(?:bug|defect|issue|problem|broken|not\s+working)\b", "general", 1.0),
    (r"\b(?:error|exception|crash|fail(?:ure|ed|s)?|throw)\b", "error", 0.9),
    (r"\b(?:null\s*(?:ref|pointer|reference)|npe|nullpointerexception)\b", "null_reference", 1.0),
    (r"\b(?:timeout|timed?\s*out|hang(?:s|ing)?|deadlock)\b", "timeout", 0.9),
    (r"\b(?:incorrect|wrong|invalid|unexpected|mismatch)\b", "logic_error", 0.7),
    (r"\b(?:missing|absent|not\s+found|404|empty)\b", "missing_data", 0.7),
    (r"\b(?:duplicate|double|repeated|redundant)\b", "duplicate", 0.8),
    (r"\b(?:regression|broke|used\s+to\s+work|worked\s+before)\b", "regression", 1.0),
    (r"\b(?:cannot|can't|unable|doesn't|does\s+not)\b", "functional", 0.6),
    (r"\b(?:500|503|502|504|internal\s+server\s+error)\b", "server_error", 0.9),
    (r"\b(?:401|403|unauthorized|forbidden|access\s+denied)\b", "auth_error", 0.9),
]

FEATURE_PATTERNS = [
    (r"\b(?:feature|implement|add|create|build|develop)\b", "new_feature", 0.7),
    (r"\b(?:enhance|improve|upgrade|extend|expand)\b", "enhancement", 0.7),
    (r"\b(?:refactor|restructure|redesign|rewrite|modernize)\b", "refactor", 0.8),
    (r"\b(?:migrate|migration|port|convert|transition)\b", "migration", 0.8),
    (r"\b(?:user\s+story|as\s+a\s+user|acceptance\s+criteria)\b", "user_story", 0.9),
]

PERFORMANCE_PATTERNS = [
    (r"\b(?:slow|performance|latency|bottleneck|lag)\b", "general", 0.9),
    (r"\b(?:memory\s+leak|out\s+of\s+memory|oom|heap)\b", "memory", 1.0),
    (r"\b(?:cpu|processor|thread|concurrency)\b", "cpu", 0.8),
    (r"\b(?:query\s+(?:slow|performance|optimization)|index)\b", "query", 0.9),
    (r"\b(?:cache|caching|redis|memcache)\b", "caching", 0.7),
    (r"\b(?:load|throughput|scalab|capacity)\b", "scalability", 0.7),
]

SECURITY_PATTERNS = [
    (r"\b(?:security|vulnerab|exploit|attack|injection)\b", "general", 1.0),
    (r"\b(?:sql\s*injection|xss|csrf|ssrf)\b", "injection", 1.0),
    (r"\b(?:auth(?:entication|orization)?|permission|role|access)\b", "auth", 0.7),
    (r"\b(?:encrypt|decrypt|hash|token|secret|credential)\b", "crypto", 0.8),
    (r"\b(?:cors|header|certificate|ssl|tls)\b", "transport", 0.7),
]

DATA_PATTERNS = [
    (r"\b(?:data\s+(?:issue|problem|error|loss|corrupt))\b", "corruption", 1.0),
    (r"\b(?:database|db|sql|query|table|column|schema)\b", "database", 0.6),
    (r"\b(?:sync|synchroniz|replicat|consistency)\b", "sync", 0.8),
    (r"\b(?:import|export|etl|transform|load\s+data)\b", "etl", 0.8),
    (r"\b(?:report|dashboard|metric|aggregate)\b", "reporting", 0.6),
    (r"\b(?:record|row|entry|field|value)\b", "record", 0.5),
]

INTEGRATION_PATTERNS = [
    (r"\b(?:api|endpoint|rest|graphql|grpc|webhook)\b", "api", 0.7),
    (r"\b(?:integrat|connect|interface|bridge|adapter)\b", "general", 0.7),
    (r"\b(?:service|microservice|module|component)\b", "service", 0.5),
    (r"\b(?:message|queue|event|publish|subscribe|kafka|rabbitmq)\b", "messaging", 0.8),
    (r"\b(?:third.?party|external|vendor|partner)\b", "external", 0.8),
]

CONFIG_PATTERNS = [
    (r"\b(?:config(?:uration)?|setting|parameter|environment)\b", "general", 0.7),
    (r"\b(?:deploy|deployment|release|pipeline|ci.?cd)\b", "deployment", 0.8),
    (r"\b(?:docker|container|kubernetes|k8s|pod)\b", "container", 0.8),
    (r"\b(?:connection\s+string|app\s*settings|env\s+var)\b", "connection", 0.9),
]


# ── Complexity Signals ───────────────────────────────────────────────────────

HIGH_COMPLEXITY_SIGNALS = [
    r"\b(?:intermittent|random|sporadic|sometimes|occasionally)\b",
    r"\b(?:race\s+condition|concurrency|thread.?safe|deadlock)\b",
    r"\b(?:distributed|cross.?service|multi.?tenant|shard)\b",
    r"\b(?:migration|backward.?compat|breaking\s+change)\b",
    r"\b(?:memory\s+leak|heap|gc|garbage\s+collect)\b",
]

LOW_COMPLEXITY_SIGNALS = [
    r"\b(?:typo|spelling|label|text|display|ui|css|style)\b",
    r"\b(?:rename|move|copy|simple|straightforward)\b",
    r"\b(?:config|setting|toggle|flag|enable|disable)\b",
]


# ── Task Classifier ─────────────────────────────────────────────────────────

class TaskClassifier:
    """
    Classifies tasks by analyzing title and description text.

    Uses weighted pattern matching across multiple category sets.
    The highest-scoring category wins, with tie-breaking favoring
    more specific categories (bug > feature > unknown).

    Domain-agnostic: works on any project without configuration.
    """

    CATEGORY_PATTERNS = {
        TaskCategory.BUG: BUG_PATTERNS,
        TaskCategory.FEATURE: FEATURE_PATTERNS,
        TaskCategory.PERFORMANCE: PERFORMANCE_PATTERNS,
        TaskCategory.SECURITY: SECURITY_PATTERNS,
        TaskCategory.DATA_ISSUE: DATA_PATTERNS,
        TaskCategory.INTEGRATION: INTEGRATION_PATTERNS,
        TaskCategory.CONFIGURATION: CONFIG_PATTERNS,
    }

    STRATEGY_MAP = {
        TaskCategory.BUG: InvestigationStrategy.ERROR_HUNT,
        TaskCategory.FEATURE: InvestigationStrategy.ARCHITECTURE,
        TaskCategory.PERFORMANCE: InvestigationStrategy.PERFORMANCE,
        TaskCategory.SECURITY: InvestigationStrategy.SECURITY_AUDIT,
        TaskCategory.DATA_ISSUE: InvestigationStrategy.DATA_ANALYSIS,
        TaskCategory.INTEGRATION: InvestigationStrategy.CODE_TRACE,
        TaskCategory.CONFIGURATION: InvestigationStrategy.CODE_TRACE,
        TaskCategory.UNKNOWN: InvestigationStrategy.FULL_STACK,
    }

    SKILL_MAP = {
        TaskCategory.BUG: [
            "repo_analysis", "code_analysis", "ticket_context",
            "sql_query", "cross_repo_analysis",
        ],
        TaskCategory.FEATURE: [
            "repo_analysis", "code_analysis", "ticket_context",
            "database_schema", "cross_repo_analysis",
        ],
        TaskCategory.PERFORMANCE: [
            "code_analysis", "sql_query", "database_schema",
            "log_analysis", "repo_analysis",
        ],
        TaskCategory.SECURITY: [
            "code_analysis", "repo_analysis", "database_schema",
            "ticket_context",
        ],
        TaskCategory.DATA_ISSUE: [
            "sql_query", "database_schema", "code_analysis",
            "repo_analysis", "ticket_context",
        ],
        TaskCategory.INTEGRATION: [
            "code_analysis", "cross_repo_analysis", "repo_analysis",
            "ticket_context", "sql_query",
        ],
        TaskCategory.CONFIGURATION: [
            "repo_analysis", "code_analysis", "ticket_context",
        ],
        TaskCategory.UNKNOWN: [
            "repo_analysis", "code_analysis", "ticket_context",
            "sql_query", "database_schema", "cross_repo_analysis",
        ],
    }

    FOCUS_MAP = {
        TaskCategory.BUG: [
            "error_patterns", "recent_commits", "code_flow_trace",
            "exception_handling", "data_validation",
        ],
        TaskCategory.FEATURE: [
            "architecture_patterns", "dependency_map", "api_contracts",
            "database_schema", "existing_implementations",
        ],
        TaskCategory.PERFORMANCE: [
            "query_execution_plans", "bottleneck_detection",
            "resource_usage", "caching_opportunities", "index_analysis",
        ],
        TaskCategory.SECURITY: [
            "input_validation", "auth_flow", "data_exposure",
            "injection_points", "permission_checks",
        ],
        TaskCategory.DATA_ISSUE: [
            "data_integrity", "foreign_key_relationships",
            "recent_data_changes", "schema_constraints", "query_results",
        ],
        TaskCategory.INTEGRATION: [
            "api_endpoints", "service_contracts", "message_flows",
            "cross_service_calls", "error_handling",
        ],
        TaskCategory.CONFIGURATION: [
            "config_files", "environment_settings", "deployment_config",
        ],
    }

    def classify(self, title: str, description: str = "",
                 task_type: str = "") -> TaskClassification:
        """
        Classify a task based on its title, description, and type.

        Args:
            title: Task title
            description: Task description (may be HTML or plain text)
            task_type: Optional task type from ticket system (Bug, User Story, etc.)

        Returns:
            TaskClassification with category, strategy, and focus areas
        """
        # Clean HTML from description
        clean_desc = re.sub(r"<[^>]+>", " ", description or "")
        text = f"{title} {clean_desc}".lower()

        # Score each category
        scores: dict[str, float] = {}
        signals: dict[str, list[str]] = {}
        sub_categories: dict[str, str] = {}

        for category, patterns in self.CATEGORY_PATTERNS.items():
            cat_score = 0.0
            cat_signals = []
            best_sub = ""
            best_sub_weight = 0.0

            for pattern_str, sub_cat, weight in patterns:
                matches = re.findall(pattern_str, text, re.IGNORECASE)
                if matches:
                    match_score = weight * min(len(matches), 3)  # Cap at 3 matches
                    cat_score += match_score
                    cat_signals.append(f"{sub_cat}:{matches[0]}")
                    if weight > best_sub_weight:
                        best_sub = sub_cat
                        best_sub_weight = weight

            if cat_score > 0:
                scores[category] = cat_score
                signals[category] = cat_signals
                sub_categories[category] = best_sub

        # Boost from ticket system type
        type_lower = task_type.lower() if task_type else ""
        if "bug" in type_lower:
            scores[TaskCategory.BUG] = scores.get(TaskCategory.BUG, 0) + 2.0
        elif "story" in type_lower or "feature" in type_lower:
            scores[TaskCategory.FEATURE] = scores.get(TaskCategory.FEATURE, 0) + 2.0
        elif "task" in type_lower:
            scores[TaskCategory.FEATURE] = scores.get(TaskCategory.FEATURE, 0) + 0.5

        # Select winner
        if not scores:
            category = TaskCategory.UNKNOWN
            confidence = 0.3
        else:
            category = max(scores, key=scores.get)
            max_score = scores[category]
            # Normalize confidence: score of 3+ = high confidence
            confidence = min(max_score / 4.0, 0.95)

        # Determine complexity
        complexity = self._assess_complexity(text)

        # Extract priority entities from title (most important words)
        priority_entities = self._extract_priority_entities(title)

        result = TaskClassification(
            category=category,
            sub_category=sub_categories.get(category, ""),
            complexity=complexity,
            confidence=confidence,
            signals=signals.get(category, []),
            suggested_skills=self.SKILL_MAP.get(category, self.SKILL_MAP[TaskCategory.UNKNOWN]),
            investigation_strategy=self.STRATEGY_MAP.get(category, InvestigationStrategy.FULL_STACK),
            focus_areas=self.FOCUS_MAP.get(category, []),
            priority_entities=priority_entities,
        )

        logger.info(
            "task_classified",
            category=result.category,
            sub_category=result.sub_category,
            strategy=result.investigation_strategy,
            complexity=result.complexity,
            confidence=f"{result.confidence:.0%}",
            signals=result.signals[:3],
            scores={k: f"{v:.1f}" for k, v in sorted(scores.items(), key=lambda x: -x[1])[:3]},
        )

        return result

    def _assess_complexity(self, text: str) -> str:
        """Assess task complexity from text signals."""
        high_score = 0
        low_score = 0

        for pattern in HIGH_COMPLEXITY_SIGNALS:
            if re.search(pattern, text, re.IGNORECASE):
                high_score += 1

        for pattern in LOW_COMPLEXITY_SIGNALS:
            if re.search(pattern, text, re.IGNORECASE):
                low_score += 1

        # Length of description is also a signal
        if len(text) > 1000:
            high_score += 1
        elif len(text) < 100:
            low_score += 1

        if high_score >= 2:
            return "high"
        elif low_score >= 2 and high_score == 0:
            return "low"
        return "medium"

    def _extract_priority_entities(self, title: str) -> list[str]:
        """Extract the most important entities from the task title."""
        entities = []

        # PascalCase identifiers (e.g., LoadPlan, TripController)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", title):
            entities.append(match.group(1))

        # Quoted strings
        for match in re.finditer(r'"([^"]+)"', title):
            entities.append(match.group(1))
        for match in re.finditer(r"'([^']+)'", title):
            entities.append(match.group(1))

        # Significant words (not stop words, 4+ chars)
        stop_words = {
            "this", "that", "with", "from", "have", "been", "will", "when",
            "what", "which", "where", "there", "their", "they", "them",
            "than", "then", "also", "just", "only", "some", "more", "most",
            "very", "much", "many", "each", "every", "both", "such", "into",
            "over", "after", "before", "about", "between", "through", "during",
            "should", "would", "could", "does", "doesn", "didn", "isn",
            "aren", "wasn", "weren", "hasn", "haven", "hadn", "won",
            "need", "needs", "able", "unable",
        }
        for word in re.findall(r"\b([a-zA-Z]{4,})\b", title):
            if word.lower() not in stop_words and word not in entities:
                entities.append(word)

        return entities[:10]
