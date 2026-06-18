"""
Pydantic schemas for Job endpoints.
Separates Create / Response / StatusResponse concerns clearly.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.job import JobStatus


# ── Request schemas ────────────────────────────────────────────────────────


class JobCreate(BaseModel):
    """Internal schema used when creating a new Job record."""
    filename: str
    file_hash: str


# ── Response schemas ───────────────────────────────────────────────────────


class JobResponse(BaseModel):
    """Returned after POST /jobs/upload."""
    job_id: uuid.UUID
    status: JobStatus
    filename: str
    created_at: datetime
    is_duplicate: bool = Field(
        default=False,
        description="True if this file was previously uploaded (idempotency hit)"
    )

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    """Returned by GET /jobs/{job_id}/status."""
    job_id: uuid.UUID
    status: JobStatus
    filename: str
    progress_percent: int
    row_count_raw: Optional[int] = None
    row_count_clean: Optional[int] = None
    anomaly_count: Optional[int] = None
    llm_calls_made: int = 0
    llm_calls_failed: int = 0
    processing_duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Included only when status=completed
    summary: Optional["SummaryBriefResponse"] = None

    model_config = {"from_attributes": True}


class JobListItem(BaseModel):
    """Single item in GET /jobs list."""
    job_id: uuid.UUID
    status: JobStatus
    filename: str
    row_count_raw: Optional[int] = None
    row_count_clean: Optional[int] = None
    anomaly_count: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated list returned by GET /jobs."""
    items: list[JobListItem]
    total: int
    limit: int
    offset: int


# ── Summary brief (embedded in status response) ────────────────────────────


class SummaryBriefResponse(BaseModel):
    """Compact summary embedded in status response when completed."""
    total_spend_inr: Optional[float] = None
    total_spend_usd: Optional[float] = None
    anomaly_count: int = 0
    risk_level: Optional[str] = None
    narrative: Optional[str] = None

    model_config = {"from_attributes": True}
