"""
Transaction model — a single cleaned row from the uploaded CSV.
Stores both original values and cleaned values, plus anomaly flags.
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, Integer, Text,
    DateTime, Numeric, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Foreign key to the parent job
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    # ── Original CSV fields (after cleaning) ──────────────────────────────
    txn_id = Column(String(50), nullable=True)       # May be null in source — assigned ORPHAN_<n>
    date = Column(String(20), nullable=True)          # ISO 8601 after normalization: YYYY-MM-DD
    merchant = Column(String(255), nullable=True)
    amount = Column(Numeric(15, 2), nullable=True)    # Decimal precision — never float for money
    currency = Column(String(10), nullable=True)      # Normalized: INR or USD
    status = Column(String(20), nullable=True)        # Normalized: SUCCESS, FAILED, PENDING
    category = Column(String(100), nullable=True)     # Original or LLM-assigned
    account_id = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Anomaly detection ─────────────────────────────────────────────────
    is_anomaly = Column(Boolean, nullable=False, default=False, index=True)
    anomaly_reason = Column(Text, nullable=True)  # Human-readable explanation

    # ── LLM classification ────────────────────────────────────────────────
    llm_category = Column(String(100), nullable=True)        # Category assigned by LLM
    llm_confidence = Column(Numeric(4, 3), nullable=True)    # 0.000–1.000
    llm_prompt_tokens = Column(Integer, nullable=True)
    llm_completion_tokens = Column(Integer, nullable=True)
    llm_failed = Column(Boolean, nullable=False, default=False)

    # ── Traceability ──────────────────────────────────────────────────────
    # Record of every transformation applied during cleaning
    # Example: [{"field": "currency", "from": "inr", "to": "INR"}, ...]
    cleaning_log = Column(JSONB, nullable=False, default=list)

    # Original CSV row number — for debugging and traceability
    row_number = Column(Integer, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationship
    job = relationship("Job", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction txn_id={self.txn_id} amount={self.amount} {self.currency} is_anomaly={self.is_anomaly}>"
