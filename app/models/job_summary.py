"""
JobSummary model — the LLM-generated narrative and aggregated spend analysis.
One-to-one with Job. Created only when processing completes successfully.
"""
import uuid
from sqlalchemy import (
    Column, String, Integer, BigInteger, Text,
    DateTime, Numeric, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # 1:1 with Job
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # ── Computed aggregates ───────────────────────────────────────────────
    total_spend_inr = Column(Numeric(18, 2), nullable=True)
    total_spend_usd = Column(Numeric(18, 2), nullable=True)
    anomaly_count = Column(Integer, nullable=False, default=0)
    transaction_count = Column(Integer, nullable=False, default=0)

    # Top merchants by total spend — stored as JSONB list
    # [{"name": "Zomato", "total_amount": 12345.67, "currency": "INR", "count": 5}]
    top_merchants = Column(JSONB, nullable=True)

    # Per-category spend breakdown
    # {"Food": 15000.00, "Travel": 8000.00, ...}
    category_breakdown = Column(JSONB, nullable=True)

    # ── LLM output ────────────────────────────────────────────────────────
    narrative = Column(Text, nullable=True)                 # 2-3 sentence human summary
    risk_level = Column(String(10), nullable=True)          # "low", "medium", "high"
    llm_raw_response = Column(JSONB, nullable=True)         # Full LLM output for debugging

    # Performance telemetry
    generation_time_ms = Column(BigInteger, nullable=True)
    llm_prompt_tokens = Column(Integer, nullable=True)
    llm_completion_tokens = Column(Integer, nullable=True)

    # Timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationship
    job = relationship("Job", back_populates="summary")

    def __repr__(self) -> str:
        return f"<JobSummary job_id={self.job_id} risk={self.risk_level} anomalies={self.anomaly_count}>"
