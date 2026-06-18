"""
Step B: Anomaly Detection Service

Three detection rules applied in priority order:

  Rule 1 — Statistical outlier:
    Group transactions by account_id. Compute median amount per account.
    Flag any transaction where amount > 3 × median.
    Reason: "Amount exceeds 3x account median (median: X, amount: Y)"

  Rule 2 — Currency-merchant mismatch:
    Flag USD transactions at merchants that are domestic-only (INR-only).
    Domestic merchant list is configurable and pre-defined from data analysis.
    Reason: "USD transaction at domestic-only merchant (expected: INR)"

  Rule 3 — Source notes cross-validation:
    If notes field contains "SUSPICIOUS" and no anomaly has been flagged yet,
    still flag the transaction with a lower-confidence reason.
    Reason: "Flagged as suspicious in source data notes"

Multiple rules can match — reasons are concatenated.
"""
from __future__ import annotations
import structlog
from decimal import Decimal
from statistics import median

from app.services.cleaning import CleanedTransaction

log = structlog.get_logger(__name__)

# Merchants known to be domestic-only (INR-denominated)
# USD transactions at these merchants are anomalous
DOMESTIC_ONLY_MERCHANTS: set[str] = {
    "swiggy",
    "ola",
    "zomato",
    "irctc",
    "jio recharge",
    "hdfc atm",
    "bookmyshow",
    "flipkart",
}

OUTLIER_MULTIPLIER = Decimal("3")  # Flag if amount > 3 × median


def _compute_account_medians(
    transactions: list[CleanedTransaction],
) -> dict[str, Decimal]:
    """
    Compute median transaction amount per account_id.
    Only includes non-None, positive amounts.
    """
    account_amounts: dict[str, list[float]] = {}
    for t in transactions:
        if t.amount is not None and t.amount > 0 and t.account_id:
            account_amounts.setdefault(t.account_id, []).append(float(t.amount))

    medians: dict[str, Decimal] = {}
    for account_id, amounts in account_amounts.items():
        if amounts:
            medians[account_id] = Decimal(str(median(amounts)))

    return medians


def detect_anomalies(
    transactions: list[CleanedTransaction],
) -> list[CleanedTransaction]:
    """
    Apply all three anomaly detection rules.
    Modifies transactions in-place (sets is_anomaly, anomaly_reason).
    Returns the same list for pipeline chaining.
    """
    account_medians = _compute_account_medians(transactions)
    anomaly_count = 0

    log.info(
        "Account medians computed",
        accounts=len(account_medians),
        medians={k: float(v) for k, v in account_medians.items()},
    )

    for txn in transactions:
        reasons: list[str] = []

        # ── Rule 1: Statistical outlier ───────────────────────────────────
        if txn.amount is not None and txn.account_id in account_medians:
            acct_median = account_medians[txn.account_id]
            threshold = OUTLIER_MULTIPLIER * acct_median
            if txn.amount > threshold:
                reasons.append(
                    f"Amount exceeds 3x account median "
                    f"(account: {txn.account_id}, median: ₹{float(acct_median):.2f}, "
                    f"amount: ₹{float(txn.amount):.2f}, threshold: ₹{float(threshold):.2f})"
                )

        # ── Rule 2: Currency-merchant mismatch ────────────────────────────
        merchant_lower = txn.merchant.lower().strip() if txn.merchant else ""
        is_domestic_merchant = any(
            domestic in merchant_lower for domestic in DOMESTIC_ONLY_MERCHANTS
        )
        if txn.currency == "USD" and is_domestic_merchant:
            reasons.append(
                f"USD transaction at domestic-only merchant '{txn.merchant}' "
                f"(expected currency: INR)"
            )

        # ── Rule 3: Source notes cross-validation ─────────────────────────
        notes_upper = txn.notes.upper() if txn.notes else ""
        if "SUSPICIOUS" in notes_upper and not reasons:
            reasons.append(
                "Flagged as suspicious in source data notes (notes: "
                f"'{txn.notes}')"
            )

        # Apply flags
        if reasons:
            txn.is_anomaly = True
            txn.anomaly_reason = "; ".join(reasons)
            anomaly_count += 1

    log.info(
        "Anomaly detection complete",
        total_transactions=len(transactions),
        anomalies_found=anomaly_count,
        anomaly_rate_pct=round(anomaly_count / len(transactions) * 100, 1) if transactions else 0,
    )

    return transactions
