"""
Job model — represents a single CSV processing job.
Tracks lifecycle from pending → processing → completed/failed.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, BigInteger, DateTime,
    Enum as SAEnum, Text, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    filename = Column(String(255), nullable=False)

    # SHA-256 hash of the uploaded file — used for idempotency
    # If the same file is uploaded twice, return the existing job
    file_hash = Column(String(64), nullable=False, unique=True, index=True)

    status = Column(
        SAEnum(JobStatus, name="job_status_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )

    # Row counts (before and after deduplication/cleaning)
    row_count_raw = Column(Integer, nullable=True)
    row_count_clean = Column(Integer, nullable=True)
    anomaly_count = Column(Integer, nullable=True, default=0)

    # Progress tracking (0–100) for polling UX
    progress_percent = Column(Integer, nullable=False, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)

    # Performance telemetry
    processing_duration_ms = Column(BigInteger, nullable=True)

    # LLM usage tracking
    llm_calls_made = Column(Integer, nullable=False, default=0)
    llm_calls_failed = Column(Integer, nullable=False, default=0)

    # Error details if status=failed
    error_message = Column(Text, nullable=True)

    # Relationships
    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", back_populates="job", uselist=False, cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Job id={self.id} status={self.status} file={self.filename}>"
