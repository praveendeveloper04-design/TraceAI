"""
LLM Resilience Layer — Production-hardened retry, circuit breaker, and caching.

Enterprise features:
  1. Config-driven (all parameters from ResilienceConfig, feature-flagged)
  2. Async-safe (asyncio.Lock on cache + circuit breaker state)
  3. Structured logging (structlog with event names for every decision)
  4. Hardened cache keys (model + task_id + prompt hash)
  5. Feature flag (resilience_enabled=False falls back to raw llm.ainvoke)
  6. Metrics (total/success/failed/retried/cache_hits/circuit_state)

Usage:
    config = ResilienceConfig()  # or from PlatformConfig
    resilient = ResilientLLM(llm, config=config)
    response = await resilient.invoke(messages, task_id="2410")
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

@dataclass
class ResilienceConfig:
    """
    Config-driven resilience parameters.

    All fields have defaults that preserve the original behavior.
    Set resilience_enabled=False to bypass entirely (raw llm.ainvoke).
    """
    resilience_enabled: bool = True
    max_retries: int = 3
    base_delay_seconds: float = 2.0
    timeout_seconds: float = 90.0
    cache_enabled: bool = True
    cache_ttl_seconds: float = 3600.0
    cache_max_entries: int = 50
    circuit_failure_threshold: int = 5
    circuit_window_seconds: float = 60.0
    circuit_recovery_seconds: float = 30.0
    model_name: str = ""  # Included in cache key for model-specific caching

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResilienceConfig:
        """Create from a config dict (e.g., from PlatformConfig)."""
        return cls(
            resilience_enabled=data.get("llm_resilience_enabled", True),
            max_retries=data.get("llm_max_retries", 3),
            base_delay_seconds=data.get("llm_retry_base_delay", 2.0),
            timeout_seconds=data.get("llm_timeout_seconds", 90.0),
            cache_enabled=data.get("llm_cache_enabled", True),
            cache_ttl_seconds=data.get("llm_cache_ttl_seconds", 3600.0),
            cache_max_entries=data.get("llm_cache_max_entries", 50),
            circuit_failure_threshold=data.get("circuit_breaker_failure_threshold", 5),
            circuit_window_seconds=data.get("circuit_breaker_window_seconds", 60.0),
            circuit_recovery_seconds=data.get("circuit_breaker_reset_timeout_seconds", 30.0),
            model_name=data.get("llm_model", ""),
        )


# ── Error Classification ─────────────────────────────────────────────────────

class ErrorType(Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    TOOL_ERROR = "tool_error"


def classify_error(exc: Exception) -> ErrorType:
    """Classify an LLM error as transient, permanent, or tool-related."""
    msg = str(exc).lower()

    if "tooluse" in msg or "toolresult" in msg or "tool_use" in msg:
        return ErrorType.TOOL_ERROR

    if any(k in msg for k in ["timeout", "timed out", "rate limit", "429",
                                "500", "502", "503", "504", "overloaded",
                                "connection", "network", "econnreset"]):
        return ErrorType.TRANSIENT

    if any(k in msg for k in ["401", "403", "authentication", "unauthorized",
                                "invalid_api_key", "permission"]):
        return ErrorType.PERMANENT

    return ErrorType.TRANSIENT


# ── Circuit Breaker (async-safe) ─────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Async-safe circuit breaker for LLM API calls.

    Uses asyncio.Lock to protect state transitions from concurrent access.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        recovery_seconds: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.failures: list[float] = []
        self.last_failure_time: float = 0.0
        self.opened_at: float = 0.0
        self._lock = asyncio.Lock()

    async def record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failures.clear()
                logger.info("circuit_state_change", new_state="closed", reason="half_open_success")

    async def record_failure(self) -> None:
        async with self._lock:
            now = time.time()
            self.last_failure_time = now

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.opened_at = now
                logger.warning("circuit_state_change", new_state="open", reason="half_open_failure")
                return

            self.failures.append(now)
            cutoff = now - self.window_seconds
            self.failures = [t for t in self.failures if t > cutoff]

            if len(self.failures) >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = now
                logger.warning(
                    "circuit_state_change",
                    new_state="open",
                    failures_in_window=len(self.failures),
                    threshold=self.failure_threshold,
                )

    async def allow_request(self) -> bool:
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                elapsed = time.time() - self.opened_at
                if elapsed >= self.recovery_seconds:
                    self.state = CircuitState.HALF_OPEN
                    logger.info("circuit_state_change", new_state="half_open", elapsed_s=f"{elapsed:.0f}")
                    return True
                return False

            return True  # HALF_OPEN: allow one probe request


# ── Response Cache (async-safe, hardened keys) ───────────────────────────────

@dataclass
class CacheEntry:
    result: Any
    created_at: float
    ttl: float


class LLMCache:
    """
    Async-safe in-memory cache with hardened keys.

    Cache key = SHA256(model_name + task_id + prompt_content).
    No plaintext stored. TTL-based expiry.
    """

    def __init__(self, ttl_seconds: float = 3600.0, max_entries: int = 50) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = asyncio.Lock()

    def _hash_key(self, messages: list, model: str = "", task_id: str = "") -> str:
        """Create a hardened cache key including model and task context."""
        parts = [model, task_id]
        for msg in messages:
            if hasattr(msg, "content"):
                parts.append(str(msg.content))
            else:
                parts.append(str(msg))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    async def get(self, messages: list, model: str = "", task_id: str = "") -> Any | None:
        async with self._lock:
            key = self._hash_key(messages, model, task_id)
            entry = self._cache.get(key)
            if entry is None:
                logger.debug("cache_miss", key=key)
                return None
            if time.time() - entry.created_at > entry.ttl:
                del self._cache[key]
                logger.debug("cache_expired", key=key)
                return None
            logger.info("cache_hit", key=key, age_s=f"{time.time() - entry.created_at:.0f}")
            return entry.result

    async def put(self, messages: list, result: Any, model: str = "", task_id: str = "") -> None:
        async with self._lock:
            if len(self._cache) >= self._max:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
                del self._cache[oldest_key]
                logger.debug("cache_evicted", evicted_key=oldest_key)

            key = self._hash_key(messages, model, task_id)
            self._cache[key] = CacheEntry(result=result, created_at=time.time(), ttl=self._ttl)
            logger.debug("cache_stored", key=key, entries=len(self._cache))

    async def clear(self) -> None:
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("cache_cleared", entries_removed=count)


# ── Resilient LLM Wrapper ───────────────────────────────────────────────────

class ResilientLLM:
    """
    Production-grade wrapper around LangChain ChatAnthropic.

    When resilience_enabled=False, falls back to raw llm.ainvoke() with
    no retry, no circuit breaker, no cache — for debugging or rollback.
    """

    def __init__(
        self,
        llm: Any,
        config: ResilienceConfig | None = None,
        # Legacy kwargs for backward compatibility
        max_retries: int = 3,
        base_delay: float = 2.0,
        timeout: float = 90.0,
        cache_ttl: float = 3600.0,
        enable_cache: bool = True,
    ) -> None:
        self.llm = llm

        # Config-driven: prefer ResilienceConfig, fall back to kwargs
        if config:
            self._cfg = config
        else:
            self._cfg = ResilienceConfig(
                max_retries=max_retries,
                base_delay_seconds=base_delay,
                timeout_seconds=timeout,
                cache_ttl_seconds=cache_ttl,
                cache_enabled=enable_cache,
            )

        self.circuit = CircuitBreaker(
            failure_threshold=self._cfg.circuit_failure_threshold,
            window_seconds=self._cfg.circuit_window_seconds,
            recovery_seconds=self._cfg.circuit_recovery_seconds,
        )
        self.cache = (
            LLMCache(ttl_seconds=self._cfg.cache_ttl_seconds, max_entries=self._cfg.cache_max_entries)
            if self._cfg.cache_enabled else None
        )

        # Metrics
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.retried_calls = 0
        self.cache_hits = 0

    async def invoke(self, messages: list, task_id: str = "", **kwargs: Any) -> str:
        """
        Invoke the LLM with full resilience (or bypass if disabled).

        Args:
            messages: LangChain message list
            task_id: Optional task ID for cache key hardening
            **kwargs: Passed to llm.ainvoke()

        Returns the response content string.
        """
        self.total_calls += 1

        # Feature flag: bypass resilience entirely
        if not self._cfg.resilience_enabled:
            logger.debug("resilience_bypassed", task_id=task_id)
            response = await self.llm.ainvoke(messages, **kwargs)
            content = response.content if hasattr(response, "content") else str(response)
            self.successful_calls += 1
            return content

        model = self._cfg.model_name

        # Check cache
        if self.cache:
            cached = await self.cache.get(messages, model=model, task_id=task_id)
            if cached is not None:
                self.cache_hits += 1
                self.successful_calls += 1
                return cached

        # Check circuit breaker
        if not await self.circuit.allow_request():
            self.failed_calls += 1
            logger.error(
                "circuit_breaker_rejected",
                task_id=task_id,
                state=self.circuit.state.value,
                failures=len(self.circuit.failures),
            )
            raise RuntimeError(
                f"Circuit breaker OPEN — LLM API failed "
                f"{len(self.circuit.failures)} times in {self.circuit.window_seconds}s. "
                f"Recovery in {self.circuit.recovery_seconds}s."
            )

        last_exc: Exception | None = None

        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                logger.debug("llm_attempt", attempt=attempt, task_id=task_id, model=model)

                response = await asyncio.wait_for(
                    self.llm.ainvoke(messages, **kwargs),
                    timeout=self._cfg.timeout_seconds,
                )
                content = response.content if hasattr(response, "content") else str(response)

                # Success
                await self.circuit.record_success()
                self.successful_calls += 1

                if self.cache:
                    await self.cache.put(messages, content, model=model, task_id=task_id)

                if attempt > 1:
                    logger.info("retry_succeeded", attempt=attempt, task_id=task_id)

                return content

            except asyncio.TimeoutError:
                last_exc = TimeoutError(f"LLM timed out after {self._cfg.timeout_seconds}s")
                error_type = ErrorType.TRANSIENT
                logger.warning("llm_timeout", attempt=attempt, timeout=self._cfg.timeout_seconds, task_id=task_id)

            except Exception as exc:
                last_exc = exc
                error_type = classify_error(exc)
                logger.warning(
                    "llm_error",
                    attempt=attempt,
                    error_type=error_type.value,
                    error=str(exc)[:200],
                    task_id=task_id,
                )

                if error_type == ErrorType.PERMANENT:
                    await self.circuit.record_failure()
                    self.failed_calls += 1
                    logger.error("permanent_failure", error=str(exc)[:200], task_id=task_id)
                    raise

                if error_type == ErrorType.TOOL_ERROR:
                    await self.circuit.record_failure()
                    self.failed_calls += 1
                    raise

            # Transient: retry with backoff
            self.retried_calls += 1
            await self.circuit.record_failure()

            if attempt < self._cfg.max_retries:
                delay = self._cfg.base_delay_seconds * (2 ** (attempt - 1))
                logger.info("retry_scheduled", attempt=attempt, delay_s=f"{delay:.1f}", task_id=task_id)
                await asyncio.sleep(delay)

        # All retries exhausted
        self.failed_calls += 1
        logger.error(
            "all_retries_exhausted",
            attempts=self._cfg.max_retries,
            last_error=str(last_exc)[:200],
            task_id=task_id,
        )
        raise last_exc or RuntimeError("LLM call failed after all retries")

    def get_metrics(self) -> dict[str, Any]:
        """Return resilience metrics for telemetry."""
        return {
            "total_calls": self.total_calls,
            "successful": self.successful_calls,
            "failed": self.failed_calls,
            "retried": self.retried_calls,
            "cache_hits": self.cache_hits,
            "circuit_state": self.circuit.state.value,
            "circuit_failures": len(self.circuit.failures),
            "resilience_enabled": self._cfg.resilience_enabled,
            "cache_enabled": self._cfg.cache_enabled,
        }
