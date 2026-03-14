"""
Rate Limiter — Prevents excessive API calls from LLM reasoning loops.

Each connector type has a minimum interval between requests.
Timeout protection wraps connector calls with configurable timeouts.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class RateLimitError(Exception):
    """Raised when a connector call is rate-limited."""


class ConnectorTimeoutError(Exception):
    """Raised when a connector call exceeds its timeout."""


class RateLimiter:
    """
    Prevents excessive API calls from LLM reasoning loops.
    Each connector type has a minimum interval between requests.
    """

    DEFAULT_INTERVALS: dict[str, int] = {
        "azure_devops": 30,   # 30 seconds between ticket API calls
        "jira": 30,
        "github_issues": 30,
        "confluence": 10,
        "salesforce": 10,
        "sql_database": 5,    # 5 seconds between DB queries
        "grafana": 10,
        "mcp": 10,
    }

    def __init__(self) -> None:
        self._last_call: dict[str, float] = defaultdict(float)

    def check(self, connector_type: str) -> bool:
        """Returns True if enough time has passed. Raises if too soon."""
        interval = self.DEFAULT_INTERVALS.get(connector_type, 10)
        elapsed = time.time() - self._last_call[connector_type]
        if elapsed < interval:
            wait = interval - elapsed
            raise RateLimitError(
                f"{connector_type}: rate limited. "
                f"Wait {wait:.0f}s (min interval: {interval}s)"
            )
        return True

    def record(self, connector_type: str) -> None:
        """Record that a call was made."""
        self._last_call[connector_type] = time.time()

    def acquire(self, connector_type: str) -> bool:
        """Check + record in one call. Use before every connector call."""
        self.check(connector_type)
        self.record(connector_type)
        return True

    def reset(self, connector_type: str | None = None) -> None:
        """Reset rate limit state. If connector_type is None, reset all."""
        if connector_type:
            self._last_call.pop(connector_type, None)
        else:
            self._last_call.clear()


# ── Connector Timeout Protection ──────────────────────────────────────────────

CONNECTOR_TIMEOUTS: dict[str, int] = {
    "repo_query": 10,        # 10 seconds for repository operations
    "ticket_query": 20,      # 20 seconds for ticket system calls
    "database_query": 30,    # 30 seconds for database queries
    "log_query": 15,         # 15 seconds for log retrieval
    "doc_search": 15,        # 15 seconds for documentation search
}


async def with_timeout(coro, timeout_key: str):
    """
    Wrap any connector call with a timeout.

    Args:
        coro: The coroutine to execute
        timeout_key: Key into CONNECTOR_TIMEOUTS for the timeout value

    Returns:
        The result of the coroutine

    Raises:
        ConnectorTimeoutError: If the operation exceeds its timeout
    """
    timeout = CONNECTOR_TIMEOUTS.get(timeout_key, 20)
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        raise ConnectorTimeoutError(
            f"Operation '{timeout_key}' timed out after {timeout}s"
        )
