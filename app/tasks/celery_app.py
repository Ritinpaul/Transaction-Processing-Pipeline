"""
Celery application configuration — Phase 4: beat schedule added.
Worker: celery -A app.tasks.celery_app worker
Beat:   celery -A app.tasks.celery_app beat
"""
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "txn_pipeline",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.process_job",
        "app.tasks.scheduled_tasks",  # Phase 4: stale job reaper + cleanup
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # One task at a time per worker — better for LLM I/O
    task_acks_late=True,           # Ack only after task completes — prevents lost tasks on crash
    broker_connection_retry_on_startup=True,  # Suppress CPendingDeprecationWarning
    # ── Beat schedule ──────────────────────────────────────────────────────
    beat_schedule={
        "reap-stale-jobs-every-5-minutes": {
            "task": "reap_stale_jobs",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
        },
        "cleanup-old-jobs-daily": {
            "task": "cleanup_old_jobs",
            "schedule": crontab(hour=2, minute=0),  # 2:00 AM UTC daily
        },
    },
)
