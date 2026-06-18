"""
AuditLog model — immutable record of every Job status transition.
Provides full observability into the pipeline lifecycle.
"""
import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    # Event classification
    event_type = Column(String(50), nullable=False)   # e.g. "status_change", "llm_call", "error"

    # Status transition tracking
    old_status = Column(String(30), nullable=True)
    new_status = Column(String(30), nullable=True)

    # Human-readable message
    message = Column(Text, nullable=True)

    # Immutable timestamp — audit logs are never updated
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # Relationship
    job = relationship("Job", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog job_id={self.job_id} event={self.event_type} {self.old_status}→{self.new_status}>"
