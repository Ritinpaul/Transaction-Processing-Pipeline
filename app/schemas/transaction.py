"""
Pydantic schemas for Transaction responses.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional, Any
from decimal import Decimal
from pydantic import BaseModel


class TransactionResponse(BaseModel):
    """Full transaction details — used in GET /jobs/{id}/results."""
    id: uuid.UUID
    job_id: uuid.UUID

    txn_id: Optional[str] = None
    date: Optional[str] = None
    merchant: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    account_id: Optional[str] = None
    notes: Optional[str] = None

    is_anomaly: bool
    anomaly_reason: Optional[str] = None

    llm_category: Optional[str] = None
    llm_confidence: Optional[Decimal] = None
    llm_failed: bool

    cleaning_log: list[Any] = []
    row_number: Optional[int] = None

    created_at: datetime

    model_config = {"from_attributes": True}


class AnomalyResponse(BaseModel):
    """Compact anomaly record — used in summary/results."""
    txn_id: Optional[str] = None
    merchant: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    anomaly_reason: Optional[str] = None
    account_id: Optional[str] = None

    model_config = {"from_attributes": True}
