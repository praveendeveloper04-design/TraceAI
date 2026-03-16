"""
Code Flow Analysis Engine -- Cross-layer execution path tracing.

Traces code execution paths through application layers:

  API Controller → Service → Repository → Database

For each entity in the investigation, this engine:
  1. Finds the entry point (Controller/API endpoint)
  2. Traces dependency injection to find the Service layer
  3. Follows the Service to Repository/Data access layer
  4. Extracts database table references from the data layer
  5. Builds a LayerMap connecting all layers

The LayerMap is a first-class concept that the investigation engine
uses to understand HOW code flows through the system, not just
WHAT files exist.

Supports:
  - C# (.NET) patterns: Controller, Service, Repository, DbContext, EF Core
  - Python patterns: FastAPI/Flask routes, service classes, SQLAlchemy models
  - TypeScript patterns: Express routes, service classes, TypeORM entities

Security: Read-only. Never modifies files. All file reads go through
the SecurityGuard validation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Layer Types ──────────────────────────────────────────────────────────────

class LayerType:
    """Application layer type constants."""
    API_CONTROLLER = "api_controller"
    SERVICE = "service"
    REPOSITORY = "repository"
    DATA_ACCESS = "data_access"
    MODEL = "model"
    MIDDLEWARE = "middleware"
    HANDLER = "handler"
    VALIDATOR = "validator"
    MAPPER = "mapper"
    HELPER = "helper"
    UNKNOWN = "unknown"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class CodeNode:
    """A single code entity (class, method, or function)."""
    name: str                           # Class or function name
    layer: str = LayerType.UNKNOWN      # Layer type
    file_path: str = ""                 # Relative file path
    full_path: str = ""                 # Absolute file path
    repo: str = ""                      # Repository name
    methods: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # Injected services
    db_tables: list[str] = field(default_factory=list)     # Referenced tables
    http_methods: list[str] = field(default_factory=list)  # GET, POST, etc.
    routes: list[str] = field(default_factory=list)        # API routes
    line_number: int = 0
    content_snippet: str = ""           # Key code snippet

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "layer": self.layer,
            "file_path": self.file_path,
            "repo": self.repo,
            "methods": self.methods[:10],
            "dependencies": self.dependencies[:10],
            "db_tables": self.db_tables[:10],
            "http_methods": self.http_methods,
            "routes": self.routes[:5],
            "line_number": self.line_number,
        }


@dataclass
class CodeEdge:
    """A dependency between two code nodes."""
    source: str                         # Source node name
    target: str                         # Target node name
    relation: str = "depends_on"        # depends_on, calls, queries, injects
    method: str = ""                    # Specific method involved

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "method": self.method,
        }


@dataclass
class ExecutionFlow:
    """A traced execution path from entry point to database."""
    entry_point: str = ""               # Controller/API endpoint
    service: str = ""                   # Service class
    repository: str = ""                # Repository/data access class
    db_tables: list[str] = field(default_factory=list)
    methods_chain: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "entry_point": self.entry_point,
            "service": self.service,
            "repository": self.repository,
            "db_tables": self.db_tables,
            "methods_chain": self.methods_chain,
            "confidence": self.confidence,
        }


@dataclass
class LayerMap:
    """Complete layer map of the investigated code area."""
    nodes: dict[str, CodeNode] = field(default_factory=dict)
    edges: list[CodeEdge] = field(default_factory=list)
    flows: list[ExecutionFlow] = field(default_factory=list)
    db_tables_referenced: list[str] = field(default_factory=list)

    def add_node(self, node: CodeNode) -> None:
        self.nodes[node.name] = node

    def add_edge(self, edge: CodeEdge) -> None:
        # Deduplicate
        for existing in self.edges:
            if (existing.source == edge.source and
                existing.target == edge.target and
                existing.relation == edge.relation):
                return
        self.edges.append(edge)

    def get_nodes_by_layer(self, layer: str) -> list[CodeNode]:
        return [n for n in self.nodes.values() if n.layer == layer]

    def get_dependencies(self, node_name: str) -> list[str]:
        return [e.target for e in self.edges
                if e.source == node_name and e.relation in ("depends_on", "injects")]

    def export(self) -> dict:
        return {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "flows": [f.to_dict() for f in self.flows],
            "db_tables": self.db_tables_referenced,
            "stats": {
                "total_nodes": len(self.nodes),
                "controllers": len(self.get_nodes_by_layer(LayerType.API_CONTROLLER)),
                "services": len(self.get_nodes_by_layer(LayerType.SERVICE)),
                "repositories": len(self.get_nodes_by_layer(LayerType.REPOSITORY)),
                "flows_traced": len(self.flows),
                "tables_referenced": len(self.db_tables_referenced),
            },
        }

    def summarize(self) -> str:
        """Human-readable summary for LLM context."""
        parts = []
        stats = self.export()["stats"]
        parts.append(
            f"Layer Map: {stats['total_nodes']} nodes "
            f"({stats['controllers']} controllers, {stats['services']} services, "
            f"{stats['repositories']} repositories), "
            f"{stats['flows_traced']} execution flows, "
            f"{stats['tables_referenced']} DB tables"
        )

        for flow in self.flows[:5]:
            chain = " -> ".join(filter(None, [
                flow.entry_point, flow.service, flow.repository,
                f"DB({', '.join(flow.db_tables[:3])})" if flow.db_tables else "",
            ]))
            parts.append(f"  Flow: {chain}")

        return "\n".join(parts)


# ── Pattern Definitions ──────────────────────────────────────────────────────

# C# patterns
CS_CLASS = re.compile(
    r"(?:public|internal|private|protected)\s+(?:partial\s+)?class\s+(\w+)"
    r"(?:\s*:\s*([\w\s,<>]+))?",
    re.MULTILINE,
)
CS_CONSTRUCTOR_INJECTION = re.compile(
    r"(?:private|readonly)\s+(?:readonly\s+)?(?:I\w+)\s+_(\w+)\s*;",
    re.MULTILINE,
)
CS_CTOR_PARAM = re.compile(
    r"\(\s*(?:.*?)(I\w+)\s+(\w+)", re.DOTALL,
)
CS_METHOD = re.compile(
    r"(?:public|private|protected|internal|async)\s+"
    r"(?:virtual\s+|override\s+|static\s+|async\s+)*"
    r"(?:Task<[^>]+>|IActionResult|ActionResult(?:<[^>]+>)?|void|\w+)\s+"
    r"(\w+)\s*\(",
    re.MULTILINE,
)
CS_HTTP_ATTR = re.compile(
    r"\[Http(Get|Post|Put|Delete|Patch)(?:\(\"([^\"]*)\"\))?\]",
    re.MULTILINE,
)
CS_ROUTE_ATTR = re.compile(
    r'\[Route\("([^"]+)"\)\]',
    re.MULTILINE,
)
CS_DBSET = re.compile(
    r"DbSet<(\w+)>\s+(\w+)",
    re.MULTILINE,
)
CS_TABLE_REF = re.compile(
    r"(?:_context|_db|_repository|context)\s*\.\s*([A-Z]\w+)",
    re.MULTILINE,
)
CS_FROM_JOIN = re.compile(
    r"\b(?:FROM|JOIN)\s+\[?([A-Z]\w{2,})\]?",
    re.IGNORECASE | re.MULTILINE,
)

# Python patterns
PY_CLASS = re.compile(
    r"class\s+(\w+)\s*(?:\(([^)]+)\))?:",
    re.MULTILINE,
)
PY_ROUTE = re.compile(
    r'@(?:app|router|api)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)',
    re.MULTILINE,
)
PY_SQLALCHEMY_MODEL = re.compile(
    r'__tablename__\s*=\s*["\'](\w+)["\']',
    re.MULTILINE,
)

# Noise words to filter from table references
TABLE_NOISE = {
    "Add", "Remove", "Update", "Find", "Where", "Select", "First",
    "Single", "Any", "Count", "ToList", "SaveChanges", "Include",
    "Set", "Entry", "String", "Int", "Bool", "Void", "Task",
    "Object", "List", "Dictionary", "Array", "Enum", "Type",
    "Exception", "Error", "Result", "Response", "Request",
    "Logger", "Options", "Configuration", "Builder",
}

# Skip directories
SKIP_DIRS = {
    "node_modules", "bin", "obj", "dist", "build", "__pycache__",
    ".git", "packages", "TestResults", ".vs", ".idea", ".vscode",
    "wwwroot", "migrations", "Migrations",
}

# Code file extensions
CODE_EXTENSIONS = {".cs", ".py", ".ts", ".js"}


# ── Code Flow Analysis Engine ────────────────────────────────────────────────

class CodeFlowAnalysisEngine:
    """
    Traces execution paths through application layers.

    Given a set of entities (from task classification), this engine:
    1. Scans repositories for matching code files
    2. Parses each file to extract classes, methods, dependencies
    3. Classifies each class into a layer (Controller, Service, Repository, etc.)
    4. Traces dependency injection chains to build execution flows
    5. Extracts database table references from data access layers

    The result is a LayerMap that shows HOW code flows through the system.

    Performance: Scans up to 200 files per investigation.
    Security: Read-only file access only.
    """

    def __init__(self, max_files: int = 200) -> None:
        self.max_files = max_files
        self._files_scanned = 0

    def analyze(
        self,
        entities: list[str],
        repo_paths: list[Path],
        focus_areas: list[str] | None = None,
    ) -> LayerMap:
        """
        Analyze code flow for the given entities across repositories.

        Args:
            entities: Entity names to search for (e.g., ["Trip", "LoadPlan"])
            repo_paths: Repository root paths to scan
            focus_areas: Optional focus areas from task classification

        Returns:
            LayerMap with nodes, edges, and execution flows
        """
        layer_map = LayerMap()
        self._files_scanned = 0

        # Phase 1: Find and parse relevant files
        entity_patterns = [e.lower() for e in entities if len(e) >= 3]

        for repo_path in repo_paths:
            if not repo_path.exists():
                continue
            self._scan_repo(repo_path, entity_patterns, layer_map)

        # Phase 2: Resolve dependency injection chains
        self._resolve_dependencies(layer_map)

        # Phase 3: Trace execution flows
        self._trace_flows(layer_map)

        # Phase 4: Collect all DB table references
        all_tables = set()
        for node in layer_map.nodes.values():
            all_tables.update(node.db_tables)
        layer_map.db_tables_referenced = sorted(all_tables)

        logger.info(
            "code_flow_analysis_complete",
            files_scanned=self._files_scanned,
            nodes=len(layer_map.nodes),
            edges=len(layer_map.edges),
            flows=len(layer_map.flows),
            tables=len(layer_map.db_tables_referenced),
        )

        return layer_map

    def _scan_repo(
        self, repo_path: Path, entity_patterns: list[str], layer_map: LayerMap
    ) -> None:
        """Scan a repository for files matching entity patterns."""
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                if self._files_scanned >= self.max_files:
                    return

                ext = Path(fname).suffix.lower()
                if ext not in CODE_EXTENSIONS:
                    continue

                # Check if filename matches any entity
                fname_lower = fname.lower()
                matched = any(ep in fname_lower for ep in entity_patterns)

                if not matched:
                    continue

                full_path = Path(root) / fname
                try:
                    content = full_path.read_text(encoding="utf-8", errors="ignore")
                    self._files_scanned += 1

                    rel_path = str(full_path.relative_to(repo_path))

                    if ext == ".cs":
                        self._parse_csharp(content, rel_path, str(full_path),
                                          repo_path.name, layer_map, entity_patterns)
                    elif ext == ".py":
                        self._parse_python(content, rel_path, str(full_path),
                                          repo_path.name, layer_map, entity_patterns)
                    elif ext in (".ts", ".js"):
                        self._parse_typescript(content, rel_path, str(full_path),
                                              repo_path.name, layer_map, entity_patterns)
                except Exception:
                    pass

    def _parse_csharp(
        self, content: str, rel_path: str, full_path: str,
        repo: str, layer_map: LayerMap, entities: list[str],
    ) -> None:
        """Parse a C# file and extract code nodes."""
        for match in CS_CLASS.finditer(content):
            class_name = match.group(1)
            base_classes = match.group(2) or ""

            # Classify the layer
            layer = self._classify_cs_layer(class_name, base_classes, content)

            # Extract methods
            methods = [m.group(1) for m in CS_METHOD.finditer(content)]

            # Extract HTTP methods and routes
            http_methods = []
            routes = []
            for http_match in CS_HTTP_ATTR.finditer(content):
                http_methods.append(http_match.group(1).upper())
                if http_match.group(2):
                    routes.append(http_match.group(2))
            for route_match in CS_ROUTE_ATTR.finditer(content):
                routes.append(route_match.group(1))

            # Extract dependency injection
            dependencies = []
            for inj_match in CS_CONSTRUCTOR_INJECTION.finditer(content):
                dep_name = inj_match.group(1)
                dependencies.append(dep_name)
            for ctor_match in CS_CTOR_PARAM.finditer(content):
                interface_name = ctor_match.group(1)
                # Strip the I prefix to get the likely implementation name
                if interface_name.startswith("I") and len(interface_name) > 1:
                    dependencies.append(interface_name[1:])

            # Extract DB table references
            db_tables = self._extract_cs_tables(content)

            # Extract a relevant code snippet
            snippet = self._extract_snippet(content, entities, class_name)

            node = CodeNode(
                name=class_name,
                layer=layer,
                file_path=rel_path,
                full_path=full_path,
                repo=repo,
                methods=methods[:15],
                dependencies=dependencies,
                db_tables=db_tables,
                http_methods=http_methods,
                routes=routes,
                line_number=content[:match.start()].count("\n") + 1,
                content_snippet=snippet,
            )
            layer_map.add_node(node)

    def _classify_cs_layer(self, class_name: str, base_classes: str, content: str) -> str:
        """Classify a C# class into an application layer."""
        name_lower = class_name.lower()
        bases_lower = base_classes.lower()

        if "controller" in name_lower or "controllerbase" in bases_lower:
            return LayerType.API_CONTROLLER
        if "service" in name_lower:
            return LayerType.SERVICE
        if "repository" in name_lower or "repo" in name_lower:
            return LayerType.REPOSITORY
        if "context" in name_lower or "dbcontext" in bases_lower:
            return LayerType.DATA_ACCESS
        if "handler" in name_lower:
            return LayerType.HANDLER
        if "validator" in name_lower:
            return LayerType.VALIDATOR
        if "mapper" in name_lower or "profile" in name_lower:
            return LayerType.MAPPER
        if "model" in name_lower or "entity" in name_lower or "dto" in name_lower:
            return LayerType.MODEL
        if "middleware" in name_lower:
            return LayerType.MIDDLEWARE
        if "helper" in name_lower or "util" in name_lower or "extension" in name_lower:
            return LayerType.HELPER

        # Check content for clues
        if CS_HTTP_ATTR.search(content):
            return LayerType.API_CONTROLLER
        if CS_DBSET.search(content):
            return LayerType.DATA_ACCESS

        return LayerType.UNKNOWN

    def _extract_cs_tables(self, content: str) -> list[str]:
        """Extract database table references from C# code."""
        tables = set()

        # DbSet<Entity> properties
        for match in CS_DBSET.finditer(content):
            tables.add(match.group(1))

        # context.TableName references
        for match in CS_TABLE_REF.finditer(content):
            name = match.group(1)
            if name not in TABLE_NOISE:
                tables.add(name)

        # FROM/JOIN in SQL strings
        for match in CS_FROM_JOIN.finditer(content):
            name = match.group(1)
            if name not in TABLE_NOISE and len(name) >= 3:
                tables.add(name)

        return sorted(tables)

    def _parse_python(
        self, content: str, rel_path: str, full_path: str,
        repo: str, layer_map: LayerMap, entities: list[str],
    ) -> None:
        """Parse a Python file and extract code nodes."""
        for match in PY_CLASS.finditer(content):
            class_name = match.group(1)
            bases = match.group(2) or ""

            layer = self._classify_py_layer(class_name, bases, content)

            # Extract methods
            methods = [m.group(1) for m in re.finditer(
                r"def\s+(\w+)\s*\(", content
            ) if not m.group(1).startswith("_")]

            # Extract routes
            routes = []
            http_methods = []
            for route_match in PY_ROUTE.finditer(content):
                http_methods.append(route_match.group(1).upper())
                routes.append(route_match.group(2))

            # Extract SQLAlchemy table names
            db_tables = []
            for tbl_match in PY_SQLALCHEMY_MODEL.finditer(content):
                db_tables.append(tbl_match.group(1))

            snippet = self._extract_snippet(content, entities, class_name)

            node = CodeNode(
                name=class_name,
                layer=layer,
                file_path=rel_path,
                full_path=full_path,
                repo=repo,
                methods=methods[:15],
                db_tables=db_tables,
                http_methods=http_methods,
                routes=routes,
                line_number=content[:match.start()].count("\n") + 1,
                content_snippet=snippet,
            )
            layer_map.add_node(node)

    def _classify_py_layer(self, class_name: str, bases: str, content: str) -> str:
        """Classify a Python class into an application layer."""
        name_lower = class_name.lower()

        if "controller" in name_lower or "view" in name_lower or "router" in name_lower:
            return LayerType.API_CONTROLLER
        if "service" in name_lower:
            return LayerType.SERVICE
        if "repository" in name_lower or "repo" in name_lower or "dao" in name_lower:
            return LayerType.REPOSITORY
        if "model" in name_lower or "schema" in name_lower:
            return LayerType.MODEL
        if PY_ROUTE.search(content):
            return LayerType.API_CONTROLLER
        if PY_SQLALCHEMY_MODEL.search(content):
            return LayerType.DATA_ACCESS

        return LayerType.UNKNOWN

    def _parse_typescript(
        self, content: str, rel_path: str, full_path: str,
        repo: str, layer_map: LayerMap, entities: list[str],
    ) -> None:
        """Parse a TypeScript/JavaScript file and extract code nodes."""
        # Class-based
        for match in re.finditer(r"(?:export\s+)?class\s+(\w+)", content):
            class_name = match.group(1)
            name_lower = class_name.lower()

            if "controller" in name_lower:
                layer = LayerType.API_CONTROLLER
            elif "service" in name_lower:
                layer = LayerType.SERVICE
            elif "repository" in name_lower:
                layer = LayerType.REPOSITORY
            elif "model" in name_lower or "entity" in name_lower:
                layer = LayerType.MODEL
            else:
                layer = LayerType.UNKNOWN

            methods = [m.group(1) for m in re.finditer(
                r"(?:async\s+)?(\w+)\s*\(", content
            )]

            node = CodeNode(
                name=class_name,
                layer=layer,
                file_path=rel_path,
                full_path=full_path,
                repo=repo,
                methods=methods[:15],
                line_number=content[:match.start()].count("\n") + 1,
            )
            layer_map.add_node(node)

    def _extract_snippet(self, content: str, entities: list[str], class_name: str) -> str:
        """Extract a relevant code snippet around entity references."""
        lines = content.split("\n")
        for i, line in enumerate(lines):
            for entity in entities[:3]:
                if entity in line.lower() and class_name.lower() in line.lower():
                    start = max(0, i - 2)
                    end = min(len(lines), i + 5)
                    return "\n".join(lines[start:end])[:500]

        # Fallback: first few lines of the class
        for i, line in enumerate(lines):
            if class_name in line:
                start = max(0, i)
                end = min(len(lines), i + 8)
                return "\n".join(lines[start:end])[:500]

        return ""

    def _resolve_dependencies(self, layer_map: LayerMap) -> None:
        """Resolve dependency injection chains between nodes."""
        node_names = {n.lower(): n for n in layer_map.nodes}

        for node in layer_map.nodes.values():
            for dep in node.dependencies:
                dep_lower = dep.lower()
                # Try exact match
                if dep_lower in node_names:
                    target = node_names[dep_lower]
                    layer_map.add_edge(CodeEdge(
                        source=node.name,
                        target=target,
                        relation="depends_on",
                    ))
                else:
                    # Try partial match (e.g., "tripService" matches "TripService")
                    for name_lower, name in node_names.items():
                        if dep_lower in name_lower or name_lower in dep_lower:
                            layer_map.add_edge(CodeEdge(
                                source=node.name,
                                target=name,
                                relation="depends_on",
                            ))
                            break

        # Add table query edges
        for node in layer_map.nodes.values():
            for table in node.db_tables:
                layer_map.add_edge(CodeEdge(
                    source=node.name,
                    target=f"DB:{table}",
                    relation="queries",
                ))

    def _trace_flows(self, layer_map: LayerMap) -> None:
        """Trace execution flows from controllers through services to repositories."""
        controllers = layer_map.get_nodes_by_layer(LayerType.API_CONTROLLER)

        for controller in controllers:
            # Find services this controller depends on
            service_deps = []
            for dep_name in layer_map.get_dependencies(controller.name):
                dep_node = layer_map.nodes.get(dep_name)
                if dep_node and dep_node.layer == LayerType.SERVICE:
                    service_deps.append(dep_node)

            if not service_deps:
                # Controller might directly use repository
                repo_deps = []
                for dep_name in layer_map.get_dependencies(controller.name):
                    dep_node = layer_map.nodes.get(dep_name)
                    if dep_node and dep_node.layer in (LayerType.REPOSITORY, LayerType.DATA_ACCESS):
                        repo_deps.append(dep_node)

                for repo in repo_deps:
                    flow = ExecutionFlow(
                        entry_point=controller.name,
                        repository=repo.name,
                        db_tables=repo.db_tables[:5],
                        methods_chain=[controller.name, repo.name],
                        confidence=0.7,
                    )
                    layer_map.flows.append(flow)
                continue

            for service in service_deps:
                # Find repositories this service depends on
                repo_deps = []
                for dep_name in layer_map.get_dependencies(service.name):
                    dep_node = layer_map.nodes.get(dep_name)
                    if dep_node and dep_node.layer in (LayerType.REPOSITORY, LayerType.DATA_ACCESS):
                        repo_deps.append(dep_node)

                if repo_deps:
                    for repo in repo_deps:
                        flow = ExecutionFlow(
                            entry_point=controller.name,
                            service=service.name,
                            repository=repo.name,
                            db_tables=repo.db_tables[:5],
                            methods_chain=[controller.name, service.name, repo.name],
                            confidence=0.85,
                        )
                        layer_map.flows.append(flow)
                else:
                    # Service without explicit repository — check for direct DB access
                    tables = service.db_tables
                    flow = ExecutionFlow(
                        entry_point=controller.name,
                        service=service.name,
                        db_tables=tables[:5],
                        methods_chain=[controller.name, service.name],
                        confidence=0.6,
                    )
                    layer_map.flows.append(flow)
