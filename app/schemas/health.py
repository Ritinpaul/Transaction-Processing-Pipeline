"""
Pydantic schemas for /health endpoint.
"""
from typing import Any
from pydantic import BaseModel


class ServiceStatus(BaseModel):
    status: str          # "up" or "down"
    error: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: str                        # "healthy" or "degraded"
    services: dict[str, Any]
    timestamp: float
