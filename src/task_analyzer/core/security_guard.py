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


# Pre-registered tools — every tool must be declared here
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

    def __init__(self, safe_mode: bool = True) -> None:
        self.safe_mode = safe_mode

    def validate_tool(self, tool_name: str, operation: str) -> bool:
        """Check tool is registered and operation is allowed."""
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

    def validate_sql_query(self, sql: str) -> str:
        """
        Validate SQL is read-only. Returns cleaned query or raises SecurityError.

        Steps:
          1. Strip comments (-- and /* */)
          2. Split on semicolons — reject compound statements
          3. First keyword must be SELECT or WITH
          4. Scan ALL tokens for BLOCKED_SQL keywords
          5. Return cleaned single statement
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

        # Step 5: Return cleaned single statement
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
    def _strip_sql_comments(sql: str) -> str:
        """Remove SQL comments to prevent bypass attacks."""
        # Remove block comments /* ... */
        result = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        # Remove line comments -- ...
        result = re.sub(r"--[^\n]*", " ", result)
        # Collapse whitespace
        result = re.sub(r"\s+", " ", result).strip()
        return result
