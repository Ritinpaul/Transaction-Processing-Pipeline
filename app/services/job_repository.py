"""
Job Repository — all database operations for the Job model.
Keeps DB logic out of the API layer (clean separation of concerns).
"""
from __future__ import annotations
import uuid
import hashlib
import structlog
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job, JobStatus
from app.models.job_summary import JobSummary
from app.models.audit_log import AuditLog

log = structlog.get_logger(__name__)


async def get_job_by_id(db: AsyncSession, job_id: uuid.UUID) -> Optional[Job]:
    """Fetch a single job by primary key. Returns None if not found."""
    result = await db.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.summary))
    )
    return result.scalar_one_or_none()


async def get_job_by_hash(db: AsyncSession, file_hash: str) -> Optional[Job]:
    """
    Look up an existing job by file SHA-256 hash.
    Used for idempotency — same file upload returns existing job.
    """
    result = await db.execute(
        select(Job).where(Job.file_hash == file_hash)
    )
    return result.scalar_one_or_none()


async def create_job(db: AsyncSession, filename: str, file_hash: str) -> Job:
    """Create a new Job record in pending state."""
    job = Job(
        filename=filename,
        file_hash=file_hash,
        status=JobStatus.PENDING,
        progress_percent=0,
    )
    db.add(job)
    await db.flush()   # Get the ID without committing yet

    # Write the initial audit log entry
    await _write_audit(db, job.id, "status_change", None, "pending", "Job created")

    await db.commit()
    await db.refresh(job)
    log.info("Job created", job_id=str(job.id), filename=filename)
    return job


async def list_jobs(
    db: AsyncSession,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Job], int]:
    """
    List jobs with optional status filter and pagination.
    Returns (items, total_count).
    """
    query = select(Job)

    if status:
        query = query.where(Job.status == status)

    # Total count (for pagination metadata)
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginated results, newest first
    query = query.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    jobs = result.scalars().all()

    return list(jobs), total


async def get_job_with_results(db: AsyncSession, job_id: uuid.UUID) -> Optional[Job]:
    """
    Fetch a job with all transactions and summary eagerly loaded.
    Used only by GET /jobs/{id}/results — heavier query.
    """
    result = await db.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.summary),
            selectinload(Job.transactions),
        )
    )
    return result.scalar_one_or_none()


async def _write_audit(
    db: AsyncSession,
    job_id: uuid.UUID,
    event_type: str,
    old_status: Optional[str],
    new_status: Optional[str],
    message: Optional[str] = None,
) -> None:
    """Internal helper — write an audit log entry."""
    log_entry = AuditLog(
        job_id=job_id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        message=message,
    )
    db.add(log_entry)


def compute_file_hash(file_bytes: bytes) -> str:
    """Compute SHA-256 hash of file bytes for idempotency checking."""
    return hashlib.sha256(file_bytes).hexdigest()
