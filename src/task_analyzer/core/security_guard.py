"""
Security Guard — Validates every operation before execution.

This module enforces TraceAI's strict read-only security model:

  ALLOWED: read files, git log, fetch tasks, SELECT queries
  BLOCKED: git commit/push/reset, file writes, INSERT/UPDATE/DELETE,
           code execution, shell commands

Every tool must be registered in the TOOL_REGISTRY with explicit
permissions. Unregistered tools are blocked unconditionally.

Safe Mode (default for all users) restricts operations to read-only
tools marked as safe=True.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class SecurityError(Exception):
    """Raised when an operation violates security policy."""


# ─── Tool Permission Registry ────────────────────────────────────────────────


@dataclass
class ToolPermission:
    """Declares what a tool is allowed to do."""

    name: str
    permission: str  # "read" only in safe mode
    safe: bool  # True = allowed in safe mode
    allowed_operations: list[str] = field(default_factory=list)


# Pre-registered tools — every tool must be declared here.
# The LLM must never directly modify files. All write operations
# go through the patch workflow with user confirmation.
TOOL_REGISTRY: dict[str, ToolPermission] = {
    "RepoReader": ToolPermission(
        name="RepoReader",
        permission="read",
        safe=True,
        allowed_operations=[
            "read_file",
            "list_files",
            "git_log",
            "git_diff",
            "git_blame",
        ],
    ),
    "TicketReader": ToolPermission(
        name="TicketReader",
        permission="read",
        safe=True,
        allowed_operations=[
            "fetch",
            "search",
            "get_detail",
            "get_context",
        ],
    ),
    "LogReader": ToolPermission(
        name="LogReader",
        permission="read",
        safe=True,
        allowed_operations=[
            "query_logs",
            "search_logs",
            "get_dashboard",
        ],
    ),
    "DBReader": ToolPermission(
        name="DBReader",
        permission="read",
        safe=True,
        allowed_operations=[
            "select_query",
            "describe_schema",
            "list_tables",
        ],
    ),
}

# Restricted tool categories — these are NEVER registered in TOOL_REGISTRY.
# Any attempt to call them raises SecurityError.
# Write operations go through the patch workflow, not through tool calls.
RESTRICTED_CATEGORIES = {
    "repo.write": "Repository writes are blocked. Use the patch workflow.",
    "filesystem.write": "Direct file writes are blocked. Use the patch workflow.",
    "database.write": "Database writes are blocked. Read-only access only.",
    "shell.execute": "Shell command execution is blocked.",
}


# ─── Security Guard ──────────────────────────────────────────────────────────


class SecurityGuard:
    """
    Validates EVERY operation before execution.
    Enforces Safe Mode (default for open-source users).

    ALLOWED: read files, git log, fetch tasks, SELECT queries
    BLOCKED: git commit/push/reset, file writes, INSERT/UPDATE/DELETE,
             code execution, shell commands
    """

    BLOCKED_GIT_OPS = frozenset({
        "commit", "push", "reset", "clean", "checkout",
        "rebase", "merge", "stash", "rm", "mv",
    })

    BLOCKED_SQL = frozenset({
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
        "MERGE", "CALL", "REPLACE", "LOAD", "RENAME",
    })

    # Objects that expose internal metadata or system procedures.
    # Checked against the full uppercase query text.
    BLOCKED_SQL_OBJECTS = (
        "INFORMATION_SCHEMA",
        "SYS.",
        "SYSOBJECTS",
        "SYSCOLUMNS",
        "SYSINDEXES",
        "SYSUSERS",
        "XP_",
        "SP_CONFIGURE",
        "SP_EXECUTESQL",
        "MASTER..",
        "MSDB..",
        "TEMPDB..",
        "OPENROWSET",
        "OPENQUERY",
        "OPENDATASOURCE",
    )

    # Feature flags for validation checks (used by traceai validate)
    sql_guard_active: bool = True
    prompt_injection_guard_active: bool = True
    patch_path_guard_active: bool = True

    def __init__(self, safe_mode: bool = True) -> None:
        self.safe_mode = safe_mode

    def validate_tool(self, tool_name: str, operation: str) -> bool:
        """Check tool is registered and operation is allowed."""
        # Check restricted categories first
        for category, reason in RESTRICTED_CATEGORIES.items():
            if tool_name.lower().startswith(category.split(".")[0]) and "write" in operation.lower():
                raise SecurityError(f"Restricted operation: {reason}")

        perm = TOOL_REGISTRY.get(tool_name)
        if not perm:
            raise SecurityError(f"Unregistered tool: {tool_name}")
        if self.safe_mode and not perm.safe:
            raise SecurityError(f"Tool {tool_name} blocked in safe mode")
        if operation not in perm.allowed_operations:
            raise SecurityError(
                f"Operation '{operation}' not allowed for {tool_name}. "
                f"Allowed: {perm.allowed_operations}"
            )
        return True

    def validate_sql_query(self, sql: str, allow_schema_inspection: bool = False) -> str:
        """
        Validate SQL is read-only. Returns cleaned query or raises SecurityError.

        Args:
            sql: The SQL query to validate.
            allow_schema_inspection: If True, permits INFORMATION_SCHEMA access
                for system-generated schema discovery queries. This flag must
                NEVER be set for user-supplied queries.

        Steps:
          1. Strip comments (-- and /* */)
          2. Split on semicolons — reject compound statements
          3. First keyword must be SELECT or WITH
          4. Scan ALL tokens for BLOCKED_SQL keywords
          5. Block system objects (unless allow_schema_inspection)
          6. Return cleaned single statement
        """
        # Step 1: Strip SQL comments
        cleaned = self._strip_sql_comments(sql)

        # Step 2: Reject compound statements (semicolons)
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        if len(statements) == 0:
            raise SecurityError("Empty SQL query")
        if len(statements) > 1:
            raise SecurityError(
                "Compound SQL statements are not allowed. "
                "Only single SELECT statements are permitted."
            )

        statement = statements[0]

        # Step 3: First keyword must be SELECT or WITH
        first_word = statement.split()[0].upper() if statement.split() else ""
        if first_word not in ("SELECT", "WITH"):
            raise SecurityError(
                f"SQL query must start with SELECT or WITH, got: {first_word}"
            )

        # Step 4: Scan ALL tokens for blocked keywords
        # Tokenize by splitting on whitespace and common delimiters
        tokens = re.findall(r"[A-Za-z_]+", statement.upper())
        for token in tokens:
            if token in self.BLOCKED_SQL:
                raise SecurityError(
                    f"Blocked SQL keyword detected: {token}. "
                    f"Only read-only queries are allowed."
                )

        # Step 5: Block access to system objects and metadata
        upper_statement = statement.upper()

        if allow_schema_inspection:
            # When schema inspection is allowed, only permit INFORMATION_SCHEMA
            # Still block all other dangerous system objects
            ALWAYS_BLOCKED = (
                "SYS.",
                "SYSOBJECTS",
                "SYSCOLUMNS",
                "SYSINDEXES",
                "SYSUSERS",
                "XP_",
                "SP_CONFIGURE",
                "SP_EXECUTESQL",
                "MASTER..",
                "MSDB..",
                "TEMPDB..",
                "OPENROWSET",
                "OPENQUERY",
                "OPENDATASOURCE",
            )
            for obj in ALWAYS_BLOCKED:
                if obj in upper_statement:
                    raise SecurityError(
                        f"Access to system object '{obj}' is blocked. "
                        f"Queries against system metadata are not permitted."
                    )
        else:
            # Full blocking — no system objects at all
            for obj in self.BLOCKED_SQL_OBJECTS:
                if obj in upper_statement:
                    raise SecurityError(
                        f"Access to system object '{obj}' is blocked. "
                        f"Queries against system metadata are not permitted."
                    )

        # Step 6: Return cleaned single statement
        return statement

    def validate_repo_operation(self, operation: str, path: str) -> bool:
        """Only allow read operations on repository files."""
        if operation.lower() in self.BLOCKED_GIT_OPS:
            raise SecurityError(
                f"Git operation '{operation}' is blocked. "
                f"TraceAI only supports read-only repository access."
            )
        return True

    def validate_file_operation(self, path: str, mode: str) -> bool:
        """Only allow read mode ('r'). Block write/append/delete."""
        if mode not in ("r", "rb"):
            raise SecurityError(
                f"File mode '{mode}' is blocked. "
                f"TraceAI only supports read-only file access (mode='r')."
            )
        return True

    @staticmethod
    def validate_patch_path(file_path: str, workspace_root: str) -> str:
        """
        Validate that a patch target path is inside the workspace.

        Prevents path traversal attacks such as:
          ../../.ssh/config
          /etc/passwd
          C:\\Windows\\System32\\...

        Returns the resolved absolute path if safe.
        Raises SecurityError if the path escapes the workspace.
        """
        from pathlib import Path as _Path

        root = _Path(workspace_root).resolve()
        resolved = (root / file_path).resolve()

        # Check the resolved path is inside the workspace
        try:
            resolved.relative_to(root)
        except ValueError:
            raise SecurityError(
                f"Path traversal blocked: '{file_path}' resolves to "
                f"'{resolved}' which is outside the workspace '{root}'."
            )

        # Block writes to dotfiles/hidden directories (.git, .ssh, .env)
        for part in resolved.relative_to(root).parts:
            if part.startswith("."):
                raise SecurityError(
                    f"Patch target blocked: '{file_path}' targets hidden "
                    f"path component '.{part[1:]}'. Patches cannot modify "
                    f"dotfiles or hidden directories."
                )

        return str(resolved)

    @staticmethod
    def _strip_sql_comments(sql: str) -> str:
        """Remove SQL comments to prevent bypass attacks."""
        # Remove block comments /* ... */
        result = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        # Remove line comments -- ...
        result = re.sub(r"--[^\n]*", " ", result)
        # Collapse whitespace
        result = re.sub(r"\s+", " ", result).strip()
        return result
