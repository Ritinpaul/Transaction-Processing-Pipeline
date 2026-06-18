"""
Celery Beat Scheduled Tasks — Phase 4 operational resilience.

Tasks:
  1. reap_stale_jobs: Every 5 minutes — finds jobs stuck in 'processing'
     for > STALE_JOB_THRESHOLD_MINUTES and marks them failed.
     Guards against worker crashes that leave jobs orphaned.

  2. cleanup_old_jobs: Daily — removes jobs older than RETENTION_DAYS
     to keep the DB lean (optional, for production hygiene).
"""
from __future__ import annotations
import uuid
import structlog
from datetime import datetime, timedelta, timezone
from celery import shared_task
from sqlalchemy import select

from app.tasks.celery_app import celery_app
from app.core.sync_database import get_sync_db
from app.models.job import Job, JobStatus
from app.models.audit_log import AuditLog

log = structlog.get_logger(__name__)

STALE_JOB_THRESHOLD_MINUTES = 15   # Jobs stuck processing for >15min are stale
RETENTION_DAYS = 30                 # Delete completed/failed jobs older than 30d


@celery_app.task(name="reap_stale_jobs")
def reap_stale_jobs():
    """
    Periodic task (every 5 min) — detects jobs stuck in 'processing'.
    A job is stale if: status=processing AND updated_at < (now - threshold).
    Marks them failed with a clear error message.
    """
    db = get_sync_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_THRESHOLD_MINUTES)
        stale_jobs = db.execute(
            select(Job).where(
                Job.status == JobStatus.PROCESSING,
                Job.updated_at < cutoff,
            )
        ).scalars().all()

        if not stale_jobs:
            log.debug("Stale job reaper: no stale jobs found")
            return {"reaped": 0}

        reaped = 0
        for job in stale_jobs:
            log.warning(
                "Reaping stale job",
                job_id=str(job.id),
                stuck_since=str(job.updated_at),
                threshold_minutes=STALE_JOB_THRESHOLD_MINUTES,
            )
            job.status = JobStatus.FAILED
            job.error_message = (
                f"Job timed out after {STALE_JOB_THRESHOLD_MINUTES} minutes in 'processing' state. "
                "Worker likely crashed. Retry with POST /jobs/{id}/retry."
            )
            db.add(AuditLog(
                job_id=job.id,
                event_type="stale_reap",
                old_status="processing",
                new_status="failed",
                message=f"Reaped by scheduler after {STALE_JOB_THRESHOLD_MINUTES}min timeout",
            ))
            reaped += 1

        db.commit()
        log.info("Stale job reaper complete", reaped=reaped)
        return {"reaped": reaped}

    except Exception as exc:
        db.rollback()
        log.error("Stale job reaper failed", error=str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="cleanup_old_jobs")
def cleanup_old_jobs():
    """
    Daily task — removes jobs older than RETENTION_DAYS that are in a terminal state.
    Terminal states: completed, failed.
    Does NOT remove pending/processing jobs regardless of age.
    """
    db = get_sync_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        old_jobs = db.execute(
            select(Job).where(
                Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                Job.created_at < cutoff,
            )
        ).scalars().all()

        if not old_jobs:
            log.debug("Cleanup: no old jobs to remove")
            return {"deleted": 0}

        deleted = 0
        for job in old_jobs:
            db.delete(job)
            deleted += 1

        db.commit()
        log.info("Old job cleanup complete", deleted=deleted, retention_days=RETENTION_DAYS)
        return {"deleted": deleted}

    except Exception as exc:
        db.rollback()
        log.error("Old job cleanup failed", error=str(exc))
        raise
    finally:
        db.close()
