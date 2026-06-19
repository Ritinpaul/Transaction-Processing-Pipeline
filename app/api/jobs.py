"""
Jobs API router — Phase 4 complete (5 endpoints).

Endpoints:
    POST  /jobs/upload          → Upload CSV, enqueue processing
    GET   /jobs/{job_id}/status → Poll job progress
    GET   /jobs/{job_id}/results → Full results once completed
    GET   /jobs/{job_id}/audit  → Full audit trail [Phase 4]
    GET   /jobs                 → List all jobs (filterable, paginated)
"""
from __future__ import annotations
import uuid
import structlog
from typing import Optional, Annotated
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException,
    Query, UploadFile, status
)
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.job import JobStatus
from app.schemas.job import (
    JobResponse, JobStatusResponse, JobListItem, JobListResponse,
    SummaryBriefResponse,
)
from app.schemas.summary import ResultsResponse, JobSummaryResponse, MerchantSummary
from app.schemas.transaction import TransactionResponse, AnomalyResponse
from app.services import job_repository
from app.models.audit_log import AuditLog
from sqlalchemy import select
from app.tasks.process_job import process_csv_job

log = structlog.get_logger(__name__)

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

REQUIRED_CSV_COLUMNS = {
    "txn_id", "date", "merchant", "amount",
    "currency", "status", "category", "account_id", "notes",
}
MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _validate_csv(upload: UploadFile) -> bytes:
    """
    Validate the uploaded file:
    - Extension must be .csv
    - MIME type must be text/csv or application/octet-stream
    - Size must be ≤ MAX_UPLOAD_SIZE_MB
    - Must contain all 9 required columns in the header row
    Returns raw file bytes on success.
    """
    # Extension check
    filename = upload.filename or ""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Only .csv files are accepted. Got: '{filename}'",
        )

    # Read file
    file_bytes = await upload.read()

    # Size check
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File too large: {len(file_bytes) / 1024 / 1024:.1f} MB. "
                f"Maximum allowed: {settings.MAX_UPLOAD_SIZE_MB} MB."
            ),
        )

    # Empty file check
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Header/column validation
    try:
        header_line = file_bytes.decode("utf-8", errors="replace").split("\n")[0]
        columns = {col.strip().lower() for col in header_line.split(",")}
        missing = REQUIRED_CSV_COLUMNS - columns
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"CSV is missing required columns: {sorted(missing)}. "
                    f"Required: {sorted(REQUIRED_CSV_COLUMNS)}"
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse CSV header: {exc}",
        )

    return file_bytes


# ── Endpoint 1: POST /jobs/upload ─────────────────────────────────────────────

@router.post(
    "/upload",
    summary="Upload a CSV file for processing",
    description=(
        "Upload a CSV file containing financial transactions. "
        "The file is validated, hashed (SHA-256), and enqueued for async processing. "
        "If the same file was previously uploaded, the existing job is returned (idempotent)."
    ),
    status_code=status.HTTP_201_CREATED,
    response_model=JobResponse,
)
async def upload_csv(
    file: UploadFile = File(..., description="CSV file with transaction data"),
    db: AsyncSession = Depends(get_db),
):
    log.info("Upload request received", filename=file.filename)

    # Step 1 — validate file
    file_bytes = await _validate_csv(file)

    # Step 2 — compute SHA-256 hash
    file_hash = job_repository.compute_file_hash(file_bytes)
    log.info("File hash computed", hash=file_hash[:12] + "...")

    # Step 3 — idempotency check: same hash → return existing job (200, not 201)
    existing_job = await job_repository.get_job_by_hash(db, file_hash)
    if existing_job:
        log.info(
            "Duplicate upload detected — returning existing job",
            job_id=str(existing_job.id),
            hash=file_hash[:12] + "...",
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "job_id": str(existing_job.id),
                "status": existing_job.status.value,
                "filename": existing_job.filename,
                "created_at": existing_job.created_at.isoformat(),
                "is_duplicate": True,
            },
        )

    # Step 4 — create new job record in DB
    job = await job_repository.create_job(
        db=db,
        filename=file.filename or "upload.csv",
        file_hash=file_hash,
    )

    # Step 5 — enqueue Celery task (pass bytes as list for JSON serialisation)
    process_csv_job.delay(str(job.id), list(file_bytes))
    log.info("Job enqueued", job_id=str(job.id))

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "job_id": str(job.id),
            "status": job.status.value,
            "filename": job.filename,
            "created_at": job.created_at.isoformat(),
            "is_duplicate": False,
        },
    )


# ── Endpoint 2: GET /jobs/{job_id}/status ─────────────────────────────────────

@router.get(
    "/{job_id}/status",
    summary="Poll job processing status",
    description=(
        "Returns the current status and progress of a job. "
        "When status=completed, a brief summary is included. "
        "When status=failed, the error message is included."
    ),
    response_model=JobStatusResponse,
)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    job = await job_repository.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # Build brief summary when completed
    summary_brief = None
    if job.status == JobStatus.COMPLETED and job.summary:
        summary_brief = SummaryBriefResponse(
            total_spend_inr=float(job.summary.total_spend_inr) if job.summary.total_spend_inr else None,
            total_spend_usd=float(job.summary.total_spend_usd) if job.summary.total_spend_usd else None,
            anomaly_count=job.summary.anomaly_count or 0,
            risk_level=job.summary.risk_level,
            narrative=job.summary.narrative,
        )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        progress_percent=job.progress_percent,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        anomaly_count=job.anomaly_count,
        llm_calls_made=job.llm_calls_made,
        llm_calls_failed=job.llm_calls_failed,
        processing_duration_ms=job.processing_duration_ms,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        summary=summary_brief,
    )


# ── Endpoint 3: GET /jobs/{job_id}/results ────────────────────────────────────

@router.get(
    "/{job_id}/results",
    summary="Get full processing results",
    description=(
        "Returns cleaned transactions, detected anomalies, category breakdown, "
        "and LLM-generated narrative. Only available when status=completed. "
        "Returns 409 if the job is still processing."
    ),
    response_model=ResultsResponse,
)
async def get_job_results(
    job_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500, description="Transactions per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
):
    job = await job_repository.get_job_with_results(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # 409 if still in progress
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job '{job_id}' is still {job.status.value} "
                f"({job.progress_percent}% complete). Poll /status first."
            ),
        )

    # 422 if failed
    if job.status == JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Job '{job_id}' failed: {job.error_message or 'Unknown error'}",
        )

    # Build summary response
    summary_resp = None
    if job.summary:
        s = job.summary
        top_merchants = []
        if s.top_merchants:
            for m in s.top_merchants:
                top_merchants.append(MerchantSummary(
                    name=m.get("name", ""),
                    total_amount=m.get("total_amount", 0),
                    currency=m.get("currency", "INR"),
                    count=m.get("count", 0),
                ))
        summary_resp = JobSummaryResponse(
            job_id=job.id,
            total_spend_inr=s.total_spend_inr,
            total_spend_usd=s.total_spend_usd,
            anomaly_count=s.anomaly_count or 0,
            transaction_count=s.transaction_count or 0,
            top_merchants=top_merchants,
            category_breakdown=s.category_breakdown or {},
            narrative=s.narrative,
            risk_level=s.risk_level,
            generation_time_ms=s.generation_time_ms,
        )

    # Paginate transactions
    all_txns = job.transactions or []
    total_txns = len(all_txns)
    paginated = all_txns[offset: offset + limit]

    txn_responses = [
        TransactionResponse(
            id=t.id,
            job_id=t.job_id,
            txn_id=t.txn_id,
            date=t.date,
            merchant=t.merchant,
            amount=t.amount,
            currency=t.currency,
            status=t.status,
            category=t.category,
            account_id=t.account_id,
            notes=t.notes,
            is_anomaly=t.is_anomaly,
            anomaly_reason=t.anomaly_reason,
            llm_category=t.llm_category,
            llm_confidence=t.llm_confidence,
            llm_failed=t.llm_failed,
            cleaning_log=t.cleaning_log or [],
            row_number=t.row_number,
            created_at=t.created_at,
        )
        for t in paginated
    ]

    # Build anomaly convenience list
    anomaly_responses = [
        AnomalyResponse(
            txn_id=t.txn_id,
            merchant=t.merchant,
            amount=t.amount,
            currency=t.currency,
            anomaly_reason=t.anomaly_reason,
            account_id=t.account_id,
        )
        for t in all_txns
        if t.is_anomaly
    ]

    return ResultsResponse(
        job_id=job.id,
        status=job.status.value,
        summary=summary_resp,
        transactions=txn_responses,
        total_transactions=total_txns,
        limit=limit,
        offset=offset,
        anomalies=anomaly_responses,
        processing_duration_ms=job.processing_duration_ms,
        llm_calls_made=job.llm_calls_made,
        llm_calls_failed=job.llm_calls_failed,
    )


# ── Endpoint 4: GET /jobs ─────────────────────────────────────────────────────

@router.get(
    "",
    summary="List all jobs",
    description=(
        "Returns a paginated list of all jobs. "
        "Filter by status using ?status=pending|processing|completed|failed. "
        "Results are sorted newest first."
    ),
    response_model=JobListResponse,
)
async def list_jobs(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by job status: pending, processing, completed, failed",
        pattern="^(pending|processing|completed|failed)$",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
):
    jobs, total = await job_repository.list_jobs(
        db=db,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    items = [
        JobListItem(
            job_id=j.id,
            status=j.status,
            filename=j.filename,
            row_count_raw=j.row_count_raw,
            row_count_clean=j.row_count_clean,
            anomaly_count=j.anomaly_count,
            created_at=j.created_at,
        )
        for j in jobs
    ]

    return JobListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )



# ── Endpoint 6: GET /jobs/{job_id}/audit ──────────────────────────────────────

@router.get(
    "/{job_id}/audit",
    summary="Get job audit trail",
    description=(
        "Returns the full chronological audit log for a job — every status transition, "
        "retry event, and stale-job reap is recorded here."
    ),
)
async def get_job_audit(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    job = await job_repository.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.job_id == job_id)
        .order_by(AuditLog.created_at.asc())
    )
    audit_logs = result.scalars().all()

    return {
        "job_id": str(job_id),
        "filename": job.filename,
        "current_status": job.status.value,
        "audit_trail": [
            {
                "id": str(entry.id),
                "event_type": entry.event_type,
                "old_status": entry.old_status,
                "new_status": entry.new_status,
                "message": entry.message,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in audit_logs
        ],
        "total_events": len(audit_logs),
    }
