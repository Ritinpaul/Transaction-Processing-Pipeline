"""
Transaction Processing Pipeline — FastAPI Application Entry Point
Phase 4: Middleware added (X-Request-ID, timing, rate limiting).
"""
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.middleware import (
    RequestIDMiddleware,
    RequestTimingMiddleware,
    UploadsRateLimitMiddleware,
)
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router

# Configure structured logging at startup
configure_logging()
log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — startup and shutdown hooks."""
    log.info("Starting Transaction Processing Pipeline", env=settings.APP_ENV)
    yield
    log.info("Shutting down Transaction Processing Pipeline")


app = FastAPI(
    title="Transaction Processing Pipeline",
    description=(
        "Async CSV transaction analysis service. Cleans financial data, "
        "detects anomalies, and generates LLM-powered narratives. "
        "Upload a CSV → poll for status → retrieve full results.\n\n"
        "**Phase 4 Features**: Circuit breaker on LLM providers, X-Request-ID correlation, "
        "upload rate limiting, stale-job reaper, audit trail endpoint."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware (order matters — outermost wraps first) ────────────────────────
# CORS must be first
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Rate limiter (applied before request ID — fail fast on abuse)
app.add_middleware(UploadsRateLimitMiddleware)
# Timing (wraps around the request ID to include middleware overhead)
app.add_middleware(RequestTimingMiddleware)
# Request ID (innermost — binds structlog context for downstream logs)
app.add_middleware(RequestIDMiddleware)


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": "Not Found", "detail": str(exc), "path": str(request.url)},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    log.error("Unhandled internal error", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "An unexpected error occurred."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health_router, tags=["Health"])
app.include_router(jobs_router, prefix="/jobs", tags=["Jobs"])
