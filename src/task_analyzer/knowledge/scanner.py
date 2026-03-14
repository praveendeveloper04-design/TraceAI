"""
Knowledge — Repository scanner and project profile generator.

Scans a local Git repository to build a lightweight knowledge profile
that the AI uses as context during investigations. The profile includes:

  - Directory structure
  - Language breakdown
  - Service/module detection
  - Database model discovery
  - Key file identification
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from task_analyzer.models.schemas import DatabaseModel, ProjectProfile, ServiceInfo

logger = structlog.get_logger(__name__)

# ── Language Detection ────────────────────────────────────────────────────────

EXTENSION_MAP: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".cs": "C#", ".csx": "C#",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".c": "C", ".h": "C",
    ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON",
    ".xml": "XML",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".less": "Less",
    ".md": "Markdown",
    ".tf": "Terraform", ".tfvars": "Terraform",
    ".bicep": "Bicep",
    ".dockerfile": "Docker",
}

# Directories to skip during scanning
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", "bin",
    "obj", ".vs", ".idea", ".vscode", "target", "vendor", ".next",
    "coverage", ".nyc_output", "packages",
}

# Patterns that indicate a service or module boundary
SERVICE_INDICATORS = {
    "Dockerfile": "container",
    "docker-compose.yml": "compose",
    "docker-compose.yaml": "compose",
    "package.json": "node",
    "setup.py": "python",
    "pyproject.toml": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "*.csproj": "dotnet",
    "*.sln": "dotnet",
    "Program.cs": "dotnet",
    "Startup.cs": "dotnet",
}

# Patterns that indicate database models
DB_MODEL_PATTERNS = {
    "models.py": "python",
    "model.py": "python",
    "schema.py": "python",
    "entities.py": "python",
    "*.entity.ts": "typescript",
    "*.model.ts": "typescript",
    "*.entity.cs": "dotnet",
    "migrations/": "migration",
}

MAX_TREE_DEPTH = 4
MAX_FILES_SCAN = 5000


class RepositoryScanner:
    """
    Scans a local Git repository and generates a ProjectProfile.

    The scanner is intentionally lightweight — it reads file metadata
    and a few key files but does NOT parse ASTs or execute code.
    """

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise ValueError(f"Not a Git repository: {self.repo_path}")
        self.repo_name = self.repo_path.name

    def scan(self) -> ProjectProfile:
        """Perform a full scan and return a ProjectProfile."""
        logger.info("scan_started", repo=self.repo_name, path=str(self.repo_path))

        files = self._collect_files()
        languages = self._detect_languages(files)
        services = self._detect_services(files)
        db_models = self._detect_database_models(files)
        key_files = self._identify_key_files(files)
        tree = self._build_directory_tree()

        primary_lang = max(languages, key=languages.get) if languages else None

        profile = ProjectProfile(
            repo_path=str(self.repo_path),
            repo_name=self.repo_name,
            primary_language=primary_lang,
            languages=languages,
            services=services,
            database_models=db_models,
            key_files=key_files,
            directory_tree=tree,
            summary=self._generate_summary(primary_lang, services, db_models),
            scanned_at=datetime.utcnow(),
        )

        logger.info(
            "scan_completed",
            repo=self.repo_name,
            files=len(files),
            services=len(services),
            languages=len(languages),
        )
        return profile

    # ── Internal Methods ──────────────────────────────────────────────────

    def _collect_files(self) -> list[Path]:
        """Walk the repo and collect all relevant files."""
        files = []
        for root, dirs, filenames in os.walk(self.repo_path):
            # Prune skipped directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if len(files) >= MAX_FILES_SCAN:
                    return files
                files.append(Path(root) / fname)
        return files

    def _detect_languages(self, files: list[Path]) -> dict[str, float]:
        """Detect language distribution by file extension."""
        counter: Counter[str] = Counter()
        for f in files:
            lang = EXTENSION_MAP.get(f.suffix.lower())
            if lang:
                counter[lang] += 1
        total = sum(counter.values()) or 1
        return {lang: count / total for lang, count in counter.most_common(15)}

    def _detect_services(self, files: list[Path]) -> list[ServiceInfo]:
        """Detect service/module boundaries in the repository."""
        services: list[ServiceInfo] = []
        seen_dirs: set[str] = set()

        for f in files:
            for indicator, framework in SERVICE_INDICATORS.items():
                if indicator.startswith("*"):
                    if f.name.endswith(indicator[1:]):
                        svc_dir = str(f.parent.relative_to(self.repo_path))
                        if svc_dir not in seen_dirs:
                            seen_dirs.add(svc_dir)
                            services.append(ServiceInfo(
                                name=f.parent.name,
                                path=svc_dir,
                                framework=framework,
                                description=f"Detected via {f.name}",
                            ))
                elif f.name == indicator:
                    svc_dir = str(f.parent.relative_to(self.repo_path))
                    if svc_dir not in seen_dirs:
                        seen_dirs.add(svc_dir)
                        services.append(ServiceInfo(
                            name=f.parent.name,
                            path=svc_dir,
                            framework=framework,
                            description=f"Detected via {indicator}",
                        ))

        return services[:30]  # Cap at 30 services

    def _detect_database_models(self, files: list[Path]) -> list[DatabaseModel]:
        """Detect database model files."""
        models: list[DatabaseModel] = []
        for f in files:
            for pattern, lang in DB_MODEL_PATTERNS.items():
                if pattern.endswith("/"):
                    if pattern.rstrip("/") in f.parts:
                        models.append(DatabaseModel(
                            name=f.stem,
                            source_file=str(f.relative_to(self.repo_path)),
                        ))
                elif pattern.startswith("*"):
                    if f.name.endswith(pattern[1:]):
                        models.append(DatabaseModel(
                            name=f.stem.replace(".entity", "").replace(".model", ""),
                            source_file=str(f.relative_to(self.repo_path)),
                        ))
                elif f.name == pattern:
                    models.append(DatabaseModel(
                        name=f.parent.name,
                        source_file=str(f.relative_to(self.repo_path)),
                    ))
        return models[:50]

    def _identify_key_files(self, files: list[Path]) -> list[str]:
        """Identify important files (configs, entry points, docs)."""
        key_patterns = {
            "README.md", "README.rst", "CHANGELOG.md",
            "Makefile", "Taskfile.yml",
            ".env.example", "docker-compose.yml", "docker-compose.yaml",
            "Dockerfile",
        }
        key_files = []
        for f in files:
            if f.name in key_patterns:
                key_files.append(str(f.relative_to(self.repo_path)))
        return key_files[:30]

    def _build_directory_tree(self, max_depth: int = MAX_TREE_DEPTH) -> str:
        """Build a compact directory tree string."""
        lines = [self.repo_name + "/"]
        self._tree_walk(self.repo_path, lines, prefix="", depth=0, max_depth=max_depth)
        return "\n".join(lines[:100])  # Cap output

    def _tree_walk(
        self, path: Path, lines: list[str], prefix: str, depth: int, max_depth: int
    ) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and not e.name.startswith(".")]

        # Show first few files at each level
        for f in files[:5]:
            lines.append(f"{prefix}├── {f.name}")
        if len(files) > 5:
            lines.append(f"{prefix}├── ... ({len(files) - 5} more files)")

        for i, d in enumerate(dirs[:15]):
            connector = "└── " if i == len(dirs[:15]) - 1 and not files else "├── "
            lines.append(f"{prefix}{connector}{d.name}/")
            new_prefix = prefix + ("    " if connector.startswith("└") else "│   ")
            self._tree_walk(d, lines, new_prefix, depth + 1, max_depth)

        if len(dirs) > 15:
            lines.append(f"{prefix}└── ... ({len(dirs) - 15} more directories)")

    def _generate_summary(
        self,
        primary_lang: str | None,
        services: list[ServiceInfo],
        db_models: list[DatabaseModel],
    ) -> str:
        """Generate a one-paragraph summary of the project."""
        parts = [f"Repository '{self.repo_name}'"]
        if primary_lang:
            parts.append(f"primarily uses {primary_lang}")
        if services:
            parts.append(f"with {len(services)} detected service(s)/module(s)")
        if db_models:
            parts.append(f"and {len(db_models)} database model(s)")
        return ". ".join(parts) + "."
