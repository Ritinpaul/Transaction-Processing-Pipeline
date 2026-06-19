"""
Step D: LLM Narrative Summary Service

Generates a structured JSON summary with:
  - Total spend by currency (INR, USD)
  - Top merchants by total spend
  - Spend breakdown by category
  - 2-3 sentence narrative
  - Risk level: low / medium / high

Falls back to computed values + generic narrative if LLM fails.
LLM output is validated through Pydantic before acceptance.
"""
from __future__ import annotations
import json
import time
import structlog
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, ValidationError

from app.core.llm_client import llm_client, AllProvidersFailedError
from app.services.cleaning import CleanedTransaction

log = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_jinja_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


# ── Pydantic validation for LLM output ───────────────────────────────────────

class MerchantItem(BaseModel):
    name: str
    total_amount: float
    currency: str
    count: int


class SummaryLLMOutput(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: list[MerchantItem]
    anomaly_count: int
    narrative: str
    risk_level: str

    def model_post_init(self, __context):
        if self.risk_level not in ("low", "medium", "high"):
            self.risk_level = "medium"


# ── Aggregate computation helpers ─────────────────────────────────────────────

def _compute_aggregates(transactions: list[CleanedTransaction]) -> dict:
    """Pre-compute all statistics needed for the LLM prompt."""
    total_inr = Decimal("0")
    total_usd = Decimal("0")
    merchant_inr: dict[str, Decimal] = defaultdict(Decimal)
    merchant_usd: dict[str, Decimal] = defaultdict(Decimal)
    merchant_count: dict[str, int] = defaultdict(int)
    category_inr: dict[str, Decimal] = defaultdict(Decimal)
    anomaly_count = 0

    for t in transactions:
        if t.amount is None:
            continue
        if t.currency == "INR":
            total_inr += t.amount
            if t.merchant:
                merchant_inr[t.merchant] += t.amount
                merchant_count[t.merchant] += 1
            if t.category:
                category_inr[t.category] += t.amount
        elif t.currency == "USD":
            total_usd += t.amount
            if t.merchant:
                merchant_usd[t.merchant] += t.amount
                merchant_count[t.merchant] += 1
        if t.is_anomaly:
            anomaly_count += 1

    # Top 5 merchants by total INR spend
    all_merchants: list[dict] = []
    all_merchant_names = set(list(merchant_inr.keys()) + list(merchant_usd.keys()))
    for m in all_merchant_names:
        inr_amt = float(merchant_inr.get(m, 0))
        usd_amt = float(merchant_usd.get(m, 0))
        # Show in primary currency
        if inr_amt >= usd_amt:
            all_merchants.append({
                "name": m, "total_amount": round(inr_amt, 2),
                "currency": "INR", "count": merchant_count[m]
            })
        else:
            all_merchants.append({
                "name": m, "total_amount": round(usd_amt, 2),
                "currency": "USD", "count": merchant_count[m]
            })

    top_merchants = sorted(all_merchants, key=lambda x: x["total_amount"], reverse=True)[:3]

    category_breakdown = {
        cat: round(float(amt), 2) for cat, amt in category_inr.items()
    }

    # Sample anomalies for the prompt
    anomalies = [
        {"txn_id": t.txn_id, "merchant": t.merchant, "amount": float(t.amount or 0),
         "currency": t.currency, "reason": t.anomaly_reason[:120]}
        for t in transactions if t.is_anomaly
    ][:5]  # Max 5 samples

    anomaly_rate = round(anomaly_count / len(transactions) * 100, 1) if transactions else 0

    return {
        "total_spend_inr": round(float(total_inr), 2),
        "total_spend_usd": round(float(total_usd), 2),
        "top_merchants": top_merchants,
        "category_breakdown": category_breakdown,
        "anomaly_count": anomaly_count,
        "anomaly_rate_pct": anomaly_rate,
        "anomalies_sample": anomalies,
        "transaction_count": len(transactions),
    }


def _compute_risk_level(anomaly_count: int, total: int, anomalies: list[CleanedTransaction]) -> str:
    """Fallback risk level computation when LLM fails."""
    if total == 0:
        return "low"
    rate = anomaly_count / total
    usd_mismatches = sum(
        1 for t in anomalies if "USD transaction at domestic" in (t.anomaly_reason or "")
    )
    if rate > 0.15 or usd_mismatches >= 3:
        return "high"
    if rate > 0.05 or usd_mismatches >= 1:
        return "medium"
    return "low"


def generate_summary(
    transactions: list[CleanedTransaction],
) -> tuple[dict, int, int]:
    """
    Generate the job summary via a single LLM call.

    Returns:
        (summary_dict, llm_calls_made, llm_calls_failed)

    summary_dict keys:
        total_spend_inr, total_spend_usd, top_merchants (list),
        category_breakdown (dict), anomaly_count, narrative,
        risk_level, llm_raw_response, generation_time_ms,
        llm_prompt_tokens, llm_completion_tokens
    """
    if not transactions:
        return {
            "total_spend_inr": 0.0, "total_spend_usd": 0.0,
            "top_merchants": [], "category_breakdown": {},
            "anomaly_count": 0, "narrative": "No transactions to analyse.",
            "risk_level": "low", "llm_raw_response": None,
            "generation_time_ms": 0, "llm_prompt_tokens": 0, "llm_completion_tokens": 0,
        }, 0, 0

    aggregates = _compute_aggregates(transactions)
    anomalies = [t for t in transactions if t.is_anomaly]

    # Render prompt template
    template = _jinja_env.get_template("summary_v1.j2")
    prompt = template.render(
        transaction_count=aggregates["transaction_count"],
        total_spend_inr=aggregates["total_spend_inr"],
        total_spend_usd=aggregates["total_spend_usd"],
        anomaly_count=aggregates["anomaly_count"],
        anomaly_rate_pct=aggregates["anomaly_rate_pct"],
        top_merchants_json=json.dumps(aggregates["top_merchants"], indent=2),
        category_breakdown_json=json.dumps(aggregates["category_breakdown"], indent=2),
        anomalies_sample_json=json.dumps(aggregates["anomalies_sample"], indent=2),
    )

    start = time.time()
    llm_calls_made = 1
    llm_calls_failed = 0
    llm_raw = None
    p_tokens = 0
    c_tokens = 0

    try:
        raw_response, p_tokens, c_tokens = llm_client.complete_json(prompt)
        llm_raw = raw_response
        generation_ms = int((time.time() - start) * 1000)

        # Validate through Pydantic
        try:
            validated = SummaryLLMOutput.model_validate(raw_response)
            log.info("LLM summary generated", risk_level=validated.risk_level, generation_ms=generation_ms)
            return {
                "total_spend_inr": validated.total_spend_inr,
                "total_spend_usd": validated.total_spend_usd,
                "top_merchants": [m.model_dump() for m in validated.top_merchants],
                "category_breakdown": aggregates["category_breakdown"],  # Use computed, more accurate
                "anomaly_count": validated.anomaly_count,
                "narrative": validated.narrative,
                "risk_level": validated.risk_level,
                "llm_raw_response": raw_response,
                "generation_time_ms": generation_ms,
                "llm_prompt_tokens": p_tokens,
                "llm_completion_tokens": c_tokens,
            }, llm_calls_made, llm_calls_failed

        except ValidationError as ve:
            log.warning("LLM summary failed Pydantic validation", errors=str(ve))
            llm_calls_failed += 1

    except Exception as exc:
        log.error("LLM summary generation failed", error=str(exc))
        llm_calls_failed += 1
        generation_ms = int((time.time() - start) * 1000)

    # ── Fallback: use computed aggregates ─────────────────────────────────
    log.info("Using fallback summary values")
    anomaly_count = aggregates["anomaly_count"]
    total = aggregates["transaction_count"]
    risk = _compute_risk_level(anomaly_count, total, anomalies)

    fallback_narrative = (
        f"Analysis of {total} transactions reveals total spend of "
        f"₹{aggregates['total_spend_inr']:,.2f} INR"
        + (f" and ${aggregates['total_spend_usd']:,.2f} USD" if aggregates["total_spend_usd"] > 0 else "")
        + f". {anomaly_count} anomalies were detected "
        f"({aggregates['anomaly_rate_pct']}% of transactions). "
        f"Risk assessment: {risk}."
    )

    return {
        "total_spend_inr": aggregates["total_spend_inr"],
        "total_spend_usd": aggregates["total_spend_usd"],
        "top_merchants": aggregates["top_merchants"],
        "category_breakdown": aggregates["category_breakdown"],
        "anomaly_count": anomaly_count,
        "narrative": fallback_narrative,
        "risk_level": risk,
        "llm_raw_response": llm_raw,
        "generation_time_ms": int((time.time() - start) * 1000),
        "llm_prompt_tokens": p_tokens,
        "llm_completion_tokens": c_tokens,
    }, llm_calls_made, llm_calls_failed
