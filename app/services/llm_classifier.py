"""
Step C: LLM Category Classification Service — Phase 4 hardened.

Changes from Phase 3:
  - Uses two-stage prompt: classify_v1.j2 → classify_v2.j2 on validation failure
  - Tracks which prompt version was used per batch (v1_used vs v2_used)
  - Pydantic validation applied to each classification result
  - AllProvidersFailedError caught gracefully — marks batch llm_failed=True
"""
from __future__ import annotations
import json
import math
import structlog
from pydantic import BaseModel, ValidationError, field_validator
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from app.core.config import settings
from app.core.llm_client import llm_client, AllProvidersFailedError
from app.services.cleaning import CleanedTransaction

log = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_jinja_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))

VALID_CATEGORIES = frozenset({
    "Food", "Shopping", "Travel", "Transport",
    "Utilities", "Cash Withdrawal", "Entertainment", "Other",
})


# ── Pydantic model for each classification result ────────────────────────────

class ClassificationResult(BaseModel):
    txn_id: str
    assigned_category: str
    confidence_score: float

    @field_validator("assigned_category")
    @classmethod
    def must_be_valid(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: '{v}'. Must be one of {sorted(VALID_CATEGORIES)}")
        return v

    @field_validator("confidence_score")
    @classmethod
    def must_be_in_range(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_prompt(batch: list[CleanedTransaction], version: str = "v1") -> str:
    txns = [
        {
            "txn_id": t.txn_id,
            "merchant": t.merchant,
            "amount": float(t.amount) if t.amount else 0.0,
            "currency": t.currency,
        }
        for t in batch
    ]
    template = _jinja_env.get_template(f"classify_{version}.j2")
    return template.render(transactions_json=json.dumps(txns, indent=2))


# ── Parse and validate ────────────────────────────────────────────────────────

def _parse_and_validate(
    raw: dict | list,
) -> dict[str, tuple[str, float]]:
    """
    Parse LLM response and validate each item with Pydantic.
    Returns {txn_id: (assigned_category, confidence_score)}.
    Invalid entries fall back to ("Other", 0.0).
    """
    items = raw if isinstance(raw, list) else []
    if not items and isinstance(raw, dict):
        # Some models wrap in a key
        for key in ("classifications", "results", "data", "transactions"):
            if key in raw:
                items = raw[key]
                break

    results: dict[str, tuple[str, float]] = {}
    for item in items:
        try:
            validated = ClassificationResult.model_validate(item)
            results[validated.txn_id] = (validated.assigned_category, validated.confidence_score)
        except ValidationError as ve:
            txn_id = item.get("txn_id", "unknown")
            log.warning("Classification item failed validation", txn_id=txn_id, error=str(ve))
            results[txn_id] = ("Other", 0.0)

    return results


# ── Main classifier ───────────────────────────────────────────────────────────

def classify_categories(
    transactions: list[CleanedTransaction],
) -> tuple[list[CleanedTransaction], int, int]:
    """
    Classify all 'Uncategorised' transactions using batched LLM calls.
    Uses v1 → v2 prompt cascade when validation fails.

    Returns:
        (updated_transactions, total_llm_calls_made, total_llm_calls_failed)
    """
    targets = [t for t in transactions if t.category == "Uncategorised"]

    if not targets:
        log.info("No uncategorised transactions — skipping LLM classification")
        return transactions, 0, 0

    batch_size = settings.BATCH_SIZE_LLM
    n_batches = math.ceil(len(targets) / batch_size)
    total_calls = 0
    failed_calls = 0

    log.info(
        "Starting LLM classification",
        targets=len(targets),
        batch_size=batch_size,
        n_batches=n_batches,
    )

    for batch_idx in range(n_batches):
        batch = targets[batch_idx * batch_size: (batch_idx + 1) * batch_size]
        prompt_v1 = _build_prompt(batch, "v1")
        prompt_v2 = _build_prompt(batch, "v2")
        total_calls += 1
        v2_was_used = False

        try:
            raw, p_tokens, c_tokens, v2_was_used = llm_client.complete_json_with_retry_prompt(
                prompt_v1, prompt_v2
            )
            classifications = _parse_and_validate(raw)

            tokens_per_txn_p = p_tokens // max(len(batch), 1)
            tokens_per_txn_c = c_tokens // max(len(batch), 1)

            for txn in batch:
                cat, conf = classifications.get(txn.txn_id, ("Other", 0.0))
                txn.category = cat
                txn.llm_category = cat
                txn.llm_confidence = conf
                txn.llm_prompt_tokens = tokens_per_txn_p
                txn.llm_completion_tokens = tokens_per_txn_c

            log.info(
                "Batch classified",
                batch=batch_idx + 1,
                of=n_batches,
                size=len(batch),
                used_v2=v2_was_used,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
            )

        except AllProvidersFailedError as exc:
            failed_calls += 1
            log.error(
                "All LLM providers failed for batch — marking llm_failed=True",
                batch=batch_idx + 1,
                error=str(exc),
            )
            for txn in batch:
                txn.llm_failed = True

        except Exception as exc:
            failed_calls += 1
            log.error(
                "Unexpected error in classification batch",
                batch=batch_idx + 1,
                error=str(exc),
            )
            for txn in batch:
                txn.llm_failed = True

    log.info(
        "Classification complete",
        targets=len(targets),
        calls_made=total_calls,
        calls_failed=failed_calls,
    )
    return transactions, total_calls, failed_calls
