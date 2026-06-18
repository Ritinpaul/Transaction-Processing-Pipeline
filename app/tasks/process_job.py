"""
Step E: Celery Pipeline Orchestration

Full async processing task:
  pending → processing → (A: clean) → (B: anomaly) → (C: llm_classify) → (D: llm_summary) → completed/failed

Progress tracking:
  0%  → task received
  10% → cleaning complete
  40% → anomaly detection complete
  60% → LLM classification complete
  85% → LLM summary complete
  100% → all saved, status=completed

Error handling:
  - Each step wrapped in try/except
  - On failure: DB rolled back, job.status=failed, traceback saved
  - DB is NEVER left in inconsistent state
"""
from __future__ import annotations
import time
import uuid
import traceback
import structlog
from datetime import datetime, timezone
from decimal import Decimal

from app.tasks.celery_app import celery_app
from app.core.sync_database import get_sync_db
from app.models.job import Job, JobStatus
from app.models.transaction import Transaction
from app.models.job_summary import JobSummary
from app.models.audit_log import AuditLog
from app.services.cleaning import clean_csv
from app.services.anomaly import detect_anomalies
from app.services.llm_classifier import classify_categories
from app.services.llm_summary import generate_summary

log = structlog.get_logger(__name__)


def _write_audit(db, job_id, event_type: str, old_status: str, new_status: str, message: str = ""):
    """Write an audit log entry (synchronous)."""
    entry = AuditLog(
        job_id=job_id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        message=message,
    )
    db.add(entry)


def _update_job_status(db, job: Job, new_status: JobStatus, progress: int = None, message: str = ""):
    """Update job status atomically and write audit log."""
    old_status = job.status.value if job.status else None
    job.status = new_status
    if progress is not None:
        job.progress_percent = progress
    _write_audit(db, job.id, "status_change", old_status, new_status.value, message)
    db.commit()
    db.refresh(job)


@celery_app.task(
    name="process_csv_job",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def process_csv_job(self, job_id: str, file_bytes_list: list):
    """
    Main CSV processing pipeline.

    Args:
        job_id:          UUID string of the Job record in DB
        file_bytes_list: File bytes serialised as list[int] for Celery JSON transport
    """
    log.info("Pipeline started", job_id=job_id)
    start_time = time.time()
    file_bytes = bytes(file_bytes_list)

    db = get_sync_db()
    try:
        # ── Fetch job ──────────────────────────────────────────────────────
        job = db.get(Job, uuid.UUID(job_id))
        if not job:
            log.error("Job not found in DB", job_id=job_id)
            db.close()
            return

        # ── Mark as processing ────────────────────────────────────────────
        _update_job_status(db, job, JobStatus.PROCESSING, progress=0,
                           message="Pipeline started")
        job.processing_started_at = datetime.now(timezone.utc)
        db.commit()

        # ════════════════════════════════════════════════════════════════════
        # STEP A: Data Cleaning
        # ════════════════════════════════════════════════════════════════════
        log.info("Step A: Data cleaning", job_id=job_id)
        try:
            transactions, raw_count = clean_csv(file_bytes)
        except Exception as exc:
            raise RuntimeError(f"Step A (cleaning) failed: {exc}") from exc

        job.row_count_raw = raw_count
        job.row_count_clean = len(transactions)
        job.progress_percent = 10
        db.commit()
        log.info("Step A complete", raw=raw_count, clean=len(transactions))

        # ════════════════════════════════════════════════════════════════════
        # STEP B: Anomaly Detection
        # ════════════════════════════════════════════════════════════════════
        log.info("Step B: Anomaly detection", job_id=job_id)
        try:
            transactions = detect_anomalies(transactions)
        except Exception as exc:
            raise RuntimeError(f"Step B (anomaly) failed: {exc}") from exc

        anomaly_count = sum(1 for t in transactions if t.is_anomaly)
        job.anomaly_count = anomaly_count
        job.progress_percent = 40
        db.commit()
        log.info("Step B complete", anomalies=anomaly_count)

        # ════════════════════════════════════════════════════════════════════
        # STEP C: LLM Category Classification
        # ════════════════════════════════════════════════════════════════════
        log.info("Step C: LLM classification", job_id=job_id)
        try:
            transactions, llm_calls, llm_fails = classify_categories(transactions)
        except Exception as exc:
            # Non-fatal — log and continue with uncategorised transactions
            log.error("Step C (LLM classify) failed — continuing without LLM categories", error=str(exc))
            llm_calls, llm_fails = 1, 1

        job.llm_calls_made += llm_calls
        job.llm_calls_failed += llm_fails
        job.progress_percent = 60
        db.commit()
        log.info("Step C complete", calls=llm_calls, failed=llm_fails)

        # ════════════════════════════════════════════════════════════════════
        # STEP D: LLM Narrative Summary
        # ════════════════════════════════════════════════════════════════════
        log.info("Step D: LLM summary", job_id=job_id)
        try:
            summary_data, s_calls, s_fails = generate_summary(transactions)
        except Exception as exc:
            log.error("Step D (LLM summary) failed — using empty summary", error=str(exc))
            summary_data = {
                "total_spend_inr": 0.0, "total_spend_usd": 0.0,
                "top_merchants": [], "category_breakdown": {},
                "anomaly_count": anomaly_count, "narrative": "Summary generation failed.",
                "risk_level": "medium", "llm_raw_response": None,
                "generation_time_ms": 0, "llm_prompt_tokens": 0, "llm_completion_tokens": 0,
            }
            s_calls, s_fails = 1, 1

        job.llm_calls_made += s_calls
        job.llm_calls_failed += s_fails
        job.progress_percent = 85
        db.commit()
        log.info("Step D complete", risk_level=summary_data.get("risk_level"))

        # ════════════════════════════════════════════════════════════════════
        # STEP E: Persist everything to DB
        # ════════════════════════════════════════════════════════════════════
        log.info("Persisting results to DB", job_id=job_id, txn_count=len(transactions))

        # Bulk insert transactions
        for txn in transactions:
            db_txn = Transaction(
                job_id=job.id,
                txn_id=txn.txn_id,
                date=txn.date,
                merchant=txn.merchant,
                amount=txn.amount,
                currency=txn.currency,
                status=txn.status,
                category=txn.category,
                account_id=txn.account_id,
                notes=txn.notes,
                is_anomaly=txn.is_anomaly,
                anomaly_reason=txn.anomaly_reason if txn.anomaly_reason else None,
                llm_category=txn.llm_category if txn.llm_category else None,
                llm_confidence=Decimal(str(txn.llm_confidence)) if txn.llm_confidence else None,
                llm_prompt_tokens=txn.llm_prompt_tokens or None,
                llm_completion_tokens=txn.llm_completion_tokens or None,
                llm_failed=txn.llm_failed,
                cleaning_log=txn.cleaning_log,
                row_number=txn.row_number,
            )
            db.add(db_txn)

        # Insert JobSummary
        job_summary = JobSummary(
            job_id=job.id,
            total_spend_inr=Decimal(str(summary_data["total_spend_inr"])),
            total_spend_usd=Decimal(str(summary_data["total_spend_usd"])),
            anomaly_count=summary_data["anomaly_count"],
            transaction_count=len(transactions),
            top_merchants=summary_data["top_merchants"],
            category_breakdown=summary_data["category_breakdown"],
            narrative=summary_data["narrative"],
            risk_level=summary_data["risk_level"],
            llm_raw_response=summary_data["llm_raw_response"],
            generation_time_ms=summary_data["generation_time_ms"],
            llm_prompt_tokens=summary_data["llm_prompt_tokens"],
            llm_completion_tokens=summary_data["llm_completion_tokens"],
        )
        db.add(job_summary)

        # Finalise job
        elapsed_ms = int((time.time() - start_time) * 1000)
        job.status = JobStatus.COMPLETED
        job.progress_percent = 100
        job.processing_duration_ms = elapsed_ms
        _write_audit(db, job.id, "status_change", "processing", "completed",
                     f"Pipeline complete in {elapsed_ms}ms")
        db.commit()

        log.info(
            "Pipeline complete",
            job_id=job_id,
            duration_ms=elapsed_ms,
            transactions=len(transactions),
            anomalies=anomaly_count,
        )

        return {
            "job_id": job_id,
            "status": "completed",
            "transactions": len(transactions),
            "anomalies": anomaly_count,
            "duration_ms": elapsed_ms,
        }

    except Exception as exc:
        # ── Global failure handler ────────────────────────────────────────
        tb = traceback.format_exc()
        log.error("Pipeline failed", job_id=job_id, error=str(exc), traceback=tb)

        try:
            db.rollback()
            job = db.get(Job, uuid.UUID(job_id))
            if job:
                job.status = JobStatus.FAILED
                job.error_message = f"{str(exc)}\n\n{tb[:2000]}"  # Truncate for DB
                _write_audit(db, job.id, "status_change", "processing", "failed", str(exc))
                db.commit()
        except Exception as inner_exc:
            log.error("Failed to save error state", error=str(inner_exc))

        raise  # Let Celery mark task as FAILURE

    finally:
        db.close()
