"""
app/models/__init__.py

Import all models here so Alembic can detect them via Base.metadata
and so they are registered with SQLAlchemy's relationship system.
"""
from app.models.job import Job, JobStatus          # noqa: F401
from app.models.transaction import Transaction     # noqa: F401
from app.models.job_summary import JobSummary      # noqa: F401
from app.models.audit_log import AuditLog          # noqa: F401

__all__ = ["Job", "JobStatus", "Transaction", "JobSummary", "AuditLog"]
