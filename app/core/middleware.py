"""
Middleware layer — Phase 4 additions:
  1. RequestIDMiddleware: generates X-Request-ID for every request and injects
     it into structlog contextvars so it appears in every log line.
  2. RequestTimingMiddleware: adds X-Process-Time-Ms header to all responses.
  3. UploadsRateLimitMiddleware: limits upload endpoint to MAX_UPLOADS_PER_MINUTE
     per client IP to prevent abuse (Redis sliding window).
"""
from __future__ import annotations
import time
import uuid
import structlog
import redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.core.config import settings

log = structlog.get_logger(__name__)

# Shared synchronous Redis client for middleware
_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# ── 1. X-Request-ID correlation middleware ────────────────────────────────────

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Generates a unique X-Request-ID for every incoming request.
    Uses the client-provided value if already present (supports tracing chains).
    Binds the ID to structlog contextvars so every log line in this request
    automatically includes request_id=<uuid>.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Bind to structlog context — all downstream log calls inherit this
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ── 2. Request timing middleware ──────────────────────────────────────────────

class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Adds X-Process-Time-Ms header to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response


# ── 3. Upload rate limiting middleware ────────────────────────────────────────

class UploadsRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter for POST /jobs/upload.
    Limits each client IP to MAX_UPLOADS_PER_MINUTE uploads per 60 seconds.
    Uses an atomic Redis INCR + EXPIRE pattern.
    Returns 429 with Retry-After header when limit exceeded.
    """

    MAX_UPLOADS_PER_MINUTE: int = 10

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate-limit upload endpoint
        if request.method == "POST" and "/jobs/upload" in request.url.path:
            client_ip = request.client.host if request.client else "unknown"
            key = f"ratelimit:upload:{client_ip}"

            try:
                r = _get_redis()
                pipe = r.pipeline()
                pipe.incr(key)
                pipe.expire(key, 60)
                count, _ = pipe.execute()

                if int(count) > self.MAX_UPLOADS_PER_MINUTE:
                    log.warning(
                        "Upload rate limit exceeded",
                        client_ip=client_ip,
                        count=count,
                        limit=self.MAX_UPLOADS_PER_MINUTE,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Rate limit exceeded",
                            "detail": (
                                f"Maximum {self.MAX_UPLOADS_PER_MINUTE} uploads per minute "
                                f"allowed per IP. Please retry after 60 seconds."
                            ),
                        },
                        headers={"Retry-After": "60"},
                    )
            except redis.RedisError:
                # Redis unavailable — allow the request (fail open)
                log.warning("Rate limiter Redis unavailable — allowing request")

        return await call_next(request)
