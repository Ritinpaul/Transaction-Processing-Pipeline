"""
Pydantic schemas for JobSummary and Results endpoint responses.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional, Any
from decimal import Decimal
from pydantic import BaseModel

from app.schemas.transaction import TransactionResponse, AnomalyResponse


class MerchantSummary(BaseModel):
    """Top merchant spend entry."""
    name: str
    total_amount: Decimal
    currency: str
    count: int


class JobSummaryResponse(BaseModel):
    """Full summary returned in GET /jobs/{id}/results."""
    job_id: uuid.UUID
    total_spend_inr: Optional[Decimal] = None
    total_spend_usd: Optional[Decimal] = None
    anomaly_count: int = 0
    transaction_count: int = 0
    top_merchants: list[MerchantSummary] = []
    category_breakdown: dict[str, float] = {}
    narrative: Optional[str] = None
    risk_level: Optional[str] = None
    generation_time_ms: Optional[int] = None

    model_config = {"from_attributes": True}


class ResultsResponse(BaseModel):
    """Full results payload returned by GET /jobs/{id}/results."""
    job_id: uuid.UUID
    status: str
    summary: Optional[JobSummaryResponse] = None

    # Paginated transaction list
    transactions: list[TransactionResponse] = []
    total_transactions: int = 0
    limit: int = 100
    offset: int = 0

    # Flagged anomalies (convenience — subset of transactions)
    anomalies: list[AnomalyResponse] = []

    # Processing metadata
    processing_duration_ms: Optional[int] = None
    llm_calls_made: int = 0
    llm_calls_failed: int = 0
