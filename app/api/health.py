"""
Health check endpoint — Phase 4 update.
Now includes LLM provider circuit breaker state + RPM counter.
Returns 200 if all healthy, 503 if any service is degraded.
"""
import time
import structlog
import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.config import settings
from app.core.llm_client import get_provider_status

router = APIRouter()
log = structlog.get_logger(__name__)


@router.get(
    "/health",
    summary="Service health check",
    description=(
        "Checks connectivity to PostgreSQL and Redis, and reports LLM provider "
        "circuit breaker state. Returns 200 if all services are reachable, 503 if degraded."
    ),
    tags=["Health"],
)
async def health_check():
    start = time.time()
    health: dict = {
        "status": "healthy",
        "services": {},
        "llm_providers": {},
        "timestamp": start,
        "version": "1.0.0",
    }

    # ── PostgreSQL ─────────────────────────────────────────────────────────
    pg_start = time.time()
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        health["services"]["postgres"] = {
            "status": "up",
            "latency_ms": round((time.time() - pg_start) * 1000, 2),
        }
    except Exception as exc:
        log.error("PostgreSQL health check failed", error=str(exc))
        health["services"]["postgres"] = {
            "status": "down",
            "error": str(exc),
            "latency_ms": round((time.time() - pg_start) * 1000, 2),
        }
        health["status"] = "degraded"

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_start = time.time()
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        await r.aclose()
        health["services"]["redis"] = {
            "status": "up",
            "latency_ms": round((time.time() - redis_start) * 1000, 2),
        }
    except Exception as exc:
        log.error("Redis health check failed", error=str(exc))
        health["services"]["redis"] = {
            "status": "down",
            "error": str(exc),
            "latency_ms": round((time.time() - redis_start) * 1000, 2),
        }
        health["status"] = "degraded"

    # ── LLM Provider Status (Phase 4) ──────────────────────────────────────
    try:
        provider_status = get_provider_status()
        health["llm_providers"] = provider_status
        # If Gemini circuit is OPEN, flag as degraded (not down)
        if provider_status.get("gemini", {}).get("circuit_state") == "open":
            health["llm_providers"]["gemini"]["warning"] = "Circuit breaker open — using OpenRouter failover"
            # Don't mark overall status as degraded — OpenRouter compensates
    except Exception as exc:
        health["llm_providers"] = {"error": str(exc)}

    # ── Response ───────────────────────────────────────────────────────────
    health["total_latency_ms"] = round((time.time() - start) * 1000, 2)

    http_status = 200 if health["status"] == "healthy" else 503
    return JSONResponse(content=health, status_code=http_status)
