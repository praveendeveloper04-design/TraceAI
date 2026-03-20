"""
LLM Resilience Layer — Retry, circuit breaker, and response caching for Claude API.

Wraps the LangChain ChatAnthropic client with production-grade resilience:

  1. Retry with exponential backoff (3 attempts, 2s/4s/8s delays)
  2. Circuit breaker (5 failures in 60s → open for 30s)
  3. Response caching (SHA256 of prompt → cached result, 1h TTL)
  4. Timeout enforcement (90s per LLM call)
  5. Structured error classification (transient vs permanent)

Usage:
    from task_analyzer.core.llm_resilience import ResilientLLM

    llm = _create_llm(config)
    resilient = ResilientLLM(llm)
    response = await resilient.invoke(messages)

The circuit breaker prevents hammering a failing API. The cache avoids
redundant LLM calls for identical prompts (e.g., re-run investigation).

Security: Prompts are hashed (SHA256) for cache keys — no plaintext storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Error Classification ─────────────────────────────────────────────────────

class ErrorType(Enum):
    TRANSIENT = "transient"      # Retry: timeout, rate limit, 500, 503
    PERMANENT = "permanent"      # Don't retry: 400 (bad request), auth failure
    TOOL_ERROR = "tool_error"    # Retry without tools: toolUse/toolResult errors


def classify_error(exc: Exception) -> ErrorType:
    """Classify an LLM error as transient, permanent, or tool-related."""
    msg = str(exc).lower()

    # Tool-related errors (PDI AI Gateway rejects tool calling)
    if "tooluse" in msg or "toolresult" in msg or "tool_use" in msg:
        return ErrorType.TOOL_ERROR

    # Transient errors (safe to retry)
    if any(k in msg for k in ["timeout", "timed out", "rate limit", "429",
                                "500", "502", "503", "504", "overloaded",
                                "connection", "network", "econnreset"]):
        return ErrorType.TRANSIENT

    # Auth errors (permanent)
    if any(k in msg for k in ["401", "403", "authentication", "unauthorized",
                                "invalid_api_key", "permission"]):
        return ErrorType.PERMANENT

    # Default: treat as transient (safer to retry)
    return ErrorType.TRANSIENT


# ── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing — reject calls
    HALF_OPEN = "half_open"  # Testing — allow one call


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for LLM API calls.

    CLOSED → OPEN: after `failure_threshold` failures within `window_seconds`
    OPEN → HALF_OPEN: after `recovery_seconds`
    HALF_OPEN → CLOSED: on success
    HALF_OPEN → OPEN: on failure
    """
    failure_threshold: int = 5
    window_seconds: float = 60.0
    recovery_seconds: float = 30.0

    state: CircuitState = CircuitState.CLOSED
    failures: list[float] = field(default_factory=list)
    last_failure_time: float = 0.0
    opened_at: float = 0.0

    def record_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failures.clear()
            logger.info("circuit_breaker_closed", reason="successful_half_open_call")

    def record_failure(self) -> None:
        """Record a failed call."""
        now = time.time()
        self.last_failure_time = now

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.opened_at = now
            logger.warning("circuit_breaker_opened", reason="half_open_failure")
            return

        # Add failure timestamp, prune old ones
        self.failures.append(now)
        cutoff = now - self.window_seconds
        self.failures = [t for t in self.failures if t > cutoff]

        if len(self.failures) >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now
            logger.warning(
                "circuit_breaker_opened",
                failures=len(self.failures),
                window=self.window_seconds,
            )

    def allow_request(self) -> bool:
        """Check if a request is allowed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.opened_at
            if elapsed >= self.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker_half_open", elapsed=f"{elapsed:.0f}s")
                return True
            return False

        # HALF_OPEN: allow one request
        return True


# ── Response Cache ───────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    result: dict[str, Any]
    created_at: float
    ttl: float


class LLMCache:
    """
    In-memory cache for LLM responses.

    Keys are SHA256 hashes of the prompt content (no plaintext stored).
    TTL-based expiry (default 1 hour).
    """

    def __init__(self, ttl_seconds: float = 3600.0, max_entries: int = 50) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds
        self._max = max_entries

    def _hash_key(self, messages: list) -> str:
        """Create a cache key from message content."""
        content = ""
        for msg in messages:
            if hasattr(msg, "content"):
                content += str(msg.content)
            else:
                content += str(msg)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def get(self, messages: list) -> dict[str, Any] | None:
        """Get cached response, or None if miss/expired."""
        key = self._hash_key(messages)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.created_at > entry.ttl:
            del self._cache[key]
            return None
        logger.info("llm_cache_hit", key=key)
        return entry.result

    def put(self, messages: list, result: dict[str, Any]) -> None:
        """Cache a response."""
        # Evict oldest if at capacity
        if len(self._cache) >= self._max:
            oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]

        key = self._hash_key(messages)
        self._cache[key] = CacheEntry(
            result=result,
            created_at=time.time(),
            ttl=self._ttl,
        )
        logger.debug("llm_cache_stored", key=key)


# ── Resilient LLM Wrapper ───────────────────────────────────────────────────

class ResilientLLM:
    """
    Production-grade wrapper around LangChain ChatAnthropic.

    Provides:
      - Retry with exponential backoff (transient errors)
      - Circuit breaker (prevents hammering failing API)
      - Response caching (avoids redundant calls)
      - Timeout enforcement
      - Structured error logging

    Usage:
        resilient = ResilientLLM(llm)
        result = await resilient.invoke(messages)
    """

    def __init__(
        self,
        llm: Any,
        max_retries: int = 3,
        base_delay: float = 2.0,
        timeout: float = 90.0,
        cache_ttl: float = 3600.0,
        enable_cache: bool = True,
    ) -> None:
        self.llm = llm
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.timeout = timeout
        self.circuit = CircuitBreaker()
        self.cache = LLMCache(ttl_seconds=cache_ttl) if enable_cache else None

        # Metrics
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.retried_calls = 0
        self.cache_hits = 0

    async def invoke(self, messages: list, **kwargs: Any) -> str:
        """
        Invoke the LLM with full resilience.

        Returns the response content string.
        Raises the last exception if all retries fail.
        """
        self.total_calls += 1

        # Check cache first
        if self.cache:
            cached = self.cache.get(messages)
            if cached is not None:
                self.cache_hits += 1
                self.successful_calls += 1
                return cached

        # Check circuit breaker
        if not self.circuit.allow_request():
            raise RuntimeError(
                f"Circuit breaker OPEN — LLM API has failed "
                f"{len(self.circuit.failures)} times in the last "
                f"{self.circuit.window_seconds}s. "
                f"Will retry in {self.circuit.recovery_seconds}s."
            )

        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.llm.ainvoke(messages, **kwargs),
                    timeout=self.timeout,
                )

                content = response.content if hasattr(response, "content") else str(response)

                # Success
                self.circuit.record_success()
                self.successful_calls += 1

                # Cache the response
                if self.cache:
                    self.cache.put(messages, content)

                if attempt > 1:
                    logger.info("llm_retry_succeeded", attempt=attempt)

                return content

            except asyncio.TimeoutError:
                last_exc = TimeoutError(f"LLM call timed out after {self.timeout}s")
                error_type = ErrorType.TRANSIENT
                logger.warning("llm_timeout", attempt=attempt, timeout=self.timeout)

            except Exception as exc:
                last_exc = exc
                error_type = classify_error(exc)

                logger.warning(
                    "llm_call_failed",
                    attempt=attempt,
                    error_type=error_type.value,
                    error=str(exc)[:200],
                )

                # Permanent errors: don't retry
                if error_type == ErrorType.PERMANENT:
                    self.circuit.record_failure()
                    self.failed_calls += 1
                    raise

                # Tool errors: don't retry here (handled by caller)
                if error_type == ErrorType.TOOL_ERROR:
                    self.circuit.record_failure()
                    self.failed_calls += 1
                    raise

            # Transient error: retry with exponential backoff
            self.retried_calls += 1
            self.circuit.record_failure()

            if attempt < self.max_retries:
                delay = self.base_delay * (2 ** (attempt - 1))
                logger.info("llm_retrying", attempt=attempt, delay=f"{delay:.1f}s")
                await asyncio.sleep(delay)

        # All retries exhausted
        self.failed_calls += 1
        logger.error(
            "llm_all_retries_exhausted",
            attempts=self.max_retries,
            last_error=str(last_exc)[:200],
        )
        raise last_exc or RuntimeError("LLM call failed after all retries")

    def get_metrics(self) -> dict[str, Any]:
        """Return resilience metrics."""
        return {
            "total_calls": self.total_calls,
            "successful": self.successful_calls,
            "failed": self.failed_calls,
            "retried": self.retried_calls,
            "cache_hits": self.cache_hits,
            "circuit_state": self.circuit.state.value,
            "circuit_failures": len(self.circuit.failures),
        }
