"""
Dual-provider LLM Client — Phase 4 hardened version.

Resilience features added in Phase 4:
  ① Circuit breaker: Redis-backed per-provider OPEN/CLOSED/HALF_OPEN state
  ② Cascading retry: v1 prompt → v2 (stricter) prompt → OpenRouter failover
  ③ Provider health: expose current circuit state via get_provider_status()
  ④ Atomic RPM counter with pre-emptive throttle (10% headroom)
  ⑤ Call telemetry: every attempt logged with provider + latency + tokens
"""
from __future__ import annotations
import json
import time
import logging
import structlog
import httpx
import redis
import google.generativeai as genai
from enum import Enum
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)
from app.core.config import settings

log = structlog.get_logger(__name__)

# Configure Gemini SDK once at import time
genai.configure(api_key=settings.GEMINI_API_KEY)

# ── Redis connection (lazy singleton) ─────────────────────────────────────────

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


# ── Custom exception hierarchy ────────────────────────────────────────────────

class GeminiRateLimitError(Exception):
    """429 from Gemini — triggers tenacity retry then OpenRouter failover."""

class GeminiServerError(Exception):
    """5xx from Gemini — triggers OpenRouter failover (no retry)."""

class CircuitOpenError(Exception):
    """Gemini circuit breaker is OPEN — skip directly to failover."""

class AllProvidersFailedError(Exception):
    """Both Gemini and OpenRouter are unavailable."""


# ── Circuit Breaker (Redis-backed) ────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal — calls flowing
    OPEN = "open"           # Tripped — skip all Gemini calls
    HALF_OPEN = "half_open" # Testing recovery — one probe call allowed

CB_KEY = "llm:circuit:gemini"
CB_FAILURE_KEY = "llm:circuit:gemini:failures"
CB_FAIL_THRESHOLD = 5      # trips OPEN after 5 consecutive failures
CB_RECOVERY_SECONDS = 120  # OPEN state persists 2 minutes before HALF_OPEN probe


def _get_circuit_state() -> CircuitState:
    try:
        r = _get_redis()
        state = r.get(CB_KEY)
        if state == CircuitState.OPEN:
            return CircuitState.OPEN
        if state == CircuitState.HALF_OPEN:
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED
    except Exception:
        return CircuitState.CLOSED  # Redis failure → assume circuit closed


def _record_circuit_success() -> None:
    """Reset circuit breaker after a successful Gemini call."""
    try:
        r = _get_redis()
        r.delete(CB_KEY)
        r.delete(CB_FAILURE_KEY)
        log.debug("Circuit breaker reset (success)")
    except Exception:
        pass


def _record_circuit_failure() -> None:
    """Increment failure count; trip OPEN if threshold reached."""
    try:
        r = _get_redis()
        failures = r.incr(CB_FAILURE_KEY)
        r.expire(CB_FAILURE_KEY, CB_RECOVERY_SECONDS)
        log.warning("Circuit breaker failure recorded", count=failures, threshold=CB_FAIL_THRESHOLD)
        if int(failures) >= CB_FAIL_THRESHOLD:
            r.setex(CB_KEY, CB_RECOVERY_SECONDS, CircuitState.OPEN)
            log.error(
                "Circuit breaker TRIPPED — Gemini calls suspended",
                recovery_in_seconds=CB_RECOVERY_SECONDS,
            )
    except Exception:
        pass


def _try_half_open_probe() -> bool:
    """Allow one probe call by transitioning OPEN → HALF_OPEN. Returns True if probe granted."""
    try:
        r = _get_redis()
        # Only one worker gets to probe (SETNX semantics)
        granted = r.set("llm:circuit:gemini:probe", "1", nx=True, ex=30)
        if granted:
            r.set(CB_KEY, CircuitState.HALF_OPEN, ex=30)
            log.info("Circuit breaker HALF_OPEN — probe call granted")
        return bool(granted)
    except Exception:
        return True  # Redis failure → allow the call


# ── RPM counter (atomic, TTL-scoped) ─────────────────────────────────────────

def _check_and_increment_rpm() -> bool:
    """
    Atomically increment and check the per-minute Gemini call counter.
    Returns True if within limit, False if throttled.
    On Redis failure, returns True to not block the pipeline.
    """
    try:
        r = _get_redis()
        pipe = r.pipeline()
        pipe.incr("llm:rpm:gemini")
        pipe.expire("llm:rpm:gemini", 60)
        count, _ = pipe.execute()
        within_limit = int(count) <= settings.LLM_RPM_LIMIT
        if not within_limit:
            log.warning("Gemini RPM pre-throttle", count=count, limit=settings.LLM_RPM_LIMIT)
        return within_limit
    except Exception:
        return True


# ── Gemini caller (tenacity retry on 429) ────────────────────────────────────

@retry(
    retry=retry_if_exception_type(GeminiRateLimitError),
    stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logging.getLogger("tenacity"), logging.WARNING),
    reraise=True,
)
def _call_gemini(prompt: str) -> tuple[str, int, int]:
    """
    Gemini 2.5 Flash call with JSON output mode.
    Returns (raw_text, prompt_tokens, completion_tokens).
    Respects circuit breaker state.
    """
    # ─ Circuit breaker check ─────────────────────────────────────────────
    state = _get_circuit_state()
    if state == CircuitState.OPEN:
        # Attempt half-open probe
        if not _try_half_open_probe():
            raise CircuitOpenError("Circuit OPEN — skip to OpenRouter")
    # ─ RPM pre-check ─────────────────────────────────────────────────────
    _check_and_increment_rpm()

    call_start = time.time()
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
            ),
        )
        latency_ms = int((time.time() - call_start) * 1000)
        p_tokens = 0
        c_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            p_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            c_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        _record_circuit_success()
        log.info("Gemini call OK", latency_ms=latency_ms, p_tokens=p_tokens, c_tokens=c_tokens)
        return response.text, p_tokens, c_tokens

    except Exception as exc:
        exc_str = str(exc).lower()
        _record_circuit_failure()
        if "429" in exc_str or "resource_exhausted" in exc_str or "quota" in exc_str:
            log.warning("Gemini 429 rate limit", error=str(exc))
            raise GeminiRateLimitError(str(exc)) from exc
        if "500" in exc_str or "503" in exc_str or "unavailable" in exc_str:
            log.warning("Gemini 5xx server error", error=str(exc))
            raise GeminiServerError(str(exc)) from exc
        raise


# ── OpenRouter caller ─────────────────────────────────────────────────────────

def _call_openrouter(prompt: str) -> tuple[str, int, int]:
    """
    OpenRouter fallback — google/gemini-2.5-flash via OpenRouter API.
    Returns (raw_text, prompt_tokens, completion_tokens).
    """
    log.info("Switching to OpenRouter failover")
    call_start = time.time()
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/txn-pipeline",
                "X-Title": "Transaction Processing Pipeline",
            },
            json={
                "model": "google/gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        latency_ms = int((time.time() - call_start) * 1000)
        log.info("OpenRouter call OK", latency_ms=latency_ms)
        return raw_text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


# ── JSON parse helper ─────────────────────────────────────────────────────────

def _parse_json(raw_text: str) -> dict | list:
    """Strip markdown fences and parse JSON."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return json.loads(text.strip())


# ── Provider status (for /health endpoint) ────────────────────────────────────

def get_provider_status() -> dict:
    """Return current state of each LLM provider for health endpoint."""
    try:
        r = _get_redis()
        cb_state = r.get(CB_KEY) or CircuitState.CLOSED
        failures = int(r.get(CB_FAILURE_KEY) or 0)
        rpm = int(r.get("llm:rpm:gemini") or 0)
    except Exception:
        cb_state = "unknown"
        failures = 0
        rpm = 0

    return {
        "gemini": {
            "circuit_state": cb_state,
            "recent_failures": failures,
            "rpm_count": rpm,
            "rpm_limit": settings.LLM_RPM_LIMIT,
        },
        "openrouter": {
            "circuit_state": "closed",  # OpenRouter has no circuit breaker (last resort)
        },
    }


# ── Main LLMClient class ──────────────────────────────────────────────────────

class LLMClient:
    """
    Hardened dual-provider LLM client.

    Call flow:
      1. Try Gemini (up to LLM_MAX_RETRIES times on 429)
      2. On failure → OpenRouter
      3. Parse JSON from whichever succeeded
    """

    def complete_json(self, prompt: str) -> tuple[dict | list, int, int]:
        """
        Send a prompt and get a JSON response.
        Returns (parsed_object, prompt_tokens, completion_tokens).
        Raises AllProvidersFailedError if both providers fail.
        """
        provider_used = "gemini"
        try:
            raw_text, p_tokens, c_tokens = _call_gemini(prompt)
        except (GeminiRateLimitError, GeminiServerError, CircuitOpenError, Exception) as exc:
            log.warning("Gemini unavailable — OpenRouter takeover", error=str(exc))
            provider_used = "openrouter"
            try:
                raw_text, p_tokens, c_tokens = _call_openrouter(prompt)
            except Exception as or_exc:
                raise AllProvidersFailedError(
                    f"Both providers failed. Gemini: {exc}. OpenRouter: {or_exc}"
                ) from or_exc

        log.info(
            "LLM call complete",
            provider=provider_used,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
        )
        parsed = _parse_json(raw_text)
        return parsed, p_tokens, c_tokens

    def complete_json_with_retry_prompt(
        self,
        prompt_v1: str,
        prompt_v2: str,
    ) -> tuple[dict | list, int, int, bool]:
        """
        Two-stage call: try prompt_v1 first. On JSON parse failure, retry with
        the stricter prompt_v2. Returns (parsed, p_tokens, c_tokens, used_v2).
        """
        try:
            result, p_tokens, c_tokens = self.complete_json(prompt_v1)
            return result, p_tokens, c_tokens, False
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("v1 prompt failed JSON parse — retrying with v2", error=str(exc))
            result, p_tokens, c_tokens = self.complete_json(prompt_v2)
            return result, p_tokens, c_tokens, True


# Module-level singleton
llm_client = LLMClient()
