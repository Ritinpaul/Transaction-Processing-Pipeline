"""
Step A: Data Cleaning Service

Handles all known dirty patterns in the transaction CSV:
  - Date format normalisation (3 variants → ISO 8601)
  - Amount cleaning ($ prefix, Decimal precision)
  - Currency/status casing normalisation
  - Missing category → "Uncategorised"
  - Missing txn_id → synthetic "ORPHAN_<row>"
  - Deduplication (hash-based, keep first occurrence)
  - Full cleaning_log per transaction for traceability

Returns a list of CleanedTransaction dataclasses.
"""
from __future__ import annotations
import csv
import hashlib
import io
import structlog
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from dateutil import parser as dateutil_parser

log = structlog.get_logger(__name__)

REQUIRED_COLUMNS = [
    "txn_id", "date", "merchant", "amount",
    "currency", "status", "category", "account_id", "notes",
]


@dataclass
class CleanedTransaction:
    """Represents a fully-cleaned transaction row, passing through all pipeline stages."""
    txn_id: str
    date: str                 # ISO 8601: YYYY-MM-DD
    merchant: str
    amount: Optional[Decimal]
    currency: str             # Normalised: INR or USD
    status: str               # Normalised: SUCCESS, FAILED, PENDING
    category: str             # Original or "Uncategorised"
    account_id: str
    notes: str
    cleaning_log: list[dict] = field(default_factory=list)
    row_number: int = 0

    # Anomaly detection fields (populated in Step B)
    is_anomaly: bool = False
    anomaly_reason: str = ""

    # LLM classification fields (populated in Step C)
    llm_category: str = ""
    llm_confidence: float = 0.0
    llm_failed: bool = False
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0


# ── Date parsing ────────────────────────────────────────────────────────────

def _parse_date(raw: str, row_num: int) -> tuple[str, list[dict]]:
    """
    Parse raw date string to ISO 8601 YYYY-MM-DD.
    Tries explicit formats first, then falls back to dateutil.
    Returns (parsed_date, cleaning_log_entries).
    """
    logs = []
    if not raw or not raw.strip():
        return "", []

    raw = raw.strip()
    original = raw

    # Explicit format priority: DD-MM-YYYY → YYYY/MM/DD → YYYY-MM-DD
    for fmt in ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            if parsed != original:
                logs.append({"field": "date", "from": original, "to": parsed, "rule": f"format_{fmt}"})
            return parsed, logs
        except ValueError:
            continue

    # Fallback: dateutil with dayfirst=True hint
    try:
        parsed = dateutil_parser.parse(raw, dayfirst=True).strftime("%Y-%m-%d")
        logs.append({"field": "date", "from": original, "to": parsed, "rule": "dateutil_fallback"})
        return parsed, logs
    except Exception:
        log.warning("Could not parse date", raw=raw, row=row_num)
        return raw, [{"field": "date", "from": original, "to": raw, "rule": "unparseable", "warning": True}]


# ── Amount parsing ───────────────────────────────────────────────────────────

def _parse_amount(raw: str, row_num: int) -> tuple[Optional[Decimal], list[dict]]:
    """
    Parse amount — strips $ prefix, commas, whitespace. Returns Decimal.
    Never returns float — Decimal preserves financial precision.
    """
    logs = []
    if not raw or not raw.strip():
        return None, [{"field": "amount", "from": raw, "to": None, "rule": "empty_amount"}]

    original = raw.strip()
    cleaned = original

    # Strip currency symbols and commas
    cleaned = cleaned.replace("$", "").replace(",", "").strip()
    if cleaned != original:
        logs.append({"field": "amount", "from": original, "to": cleaned, "rule": "strip_currency_symbol"})

    try:
        return Decimal(cleaned), logs
    except InvalidOperation:
        log.warning("Invalid amount", raw=raw, row=row_num)
        return None, logs + [{"field": "amount", "from": original, "to": None, "rule": "invalid_decimal", "warning": True}]


# ── Deduplication ────────────────────────────────────────────────────────────

def _row_hash(txn_id: str, date: str, merchant: str, amount: Optional[Decimal], account_id: str) -> str:
    """Deterministic hash of key fields for deduplication."""
    key = f"{txn_id}|{date}|{merchant}|{amount}|{account_id}"
    return hashlib.md5(key.encode()).hexdigest()


# ── Main cleaning function ───────────────────────────────────────────────────

def clean_csv(file_bytes: bytes) -> tuple[list[CleanedTransaction], int]:
    """
    Parse and clean a CSV file.
    Returns (cleaned_transactions, raw_row_count).

    Applies:
      1. Date normalisation
      2. Amount cleaning (Decimal, strip $)
      3. Currency normalisation (.upper())
      4. Status normalisation (.upper())
      5. Category → "Uncategorised" if blank
      6. txn_id → "ORPHAN_<row>" if blank
      7. Hash-based deduplication (keep first occurrence)
      8. Full cleaning_log per row
    """
    text = file_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    seen_hashes: set[str] = set()
    transactions: list[CleanedTransaction] = []
    raw_row_count = 0

    for row_num, row in enumerate(reader, start=2):  # row 2 = first data row
        raw_row_count += 1
        cleaning_log: list[dict] = []

        # ── txn_id ──────────────────────────────────────────────────────────
        txn_id = (row.get("txn_id") or "").strip()
        if not txn_id:
            synthetic = f"ORPHAN_{row_num}"
            cleaning_log.append({"field": "txn_id", "from": "", "to": synthetic, "rule": "synthetic_id"})
            txn_id = synthetic

        # ── Date ────────────────────────────────────────────────────────────
        raw_date = (row.get("date") or "").strip()
        date, date_logs = _parse_date(raw_date, row_num)
        cleaning_log.extend(date_logs)

        # ── Merchant ─────────────────────────────────────────────────────────
        merchant = (row.get("merchant") or "").strip()

        # ── Amount ───────────────────────────────────────────────────────────
        raw_amount = (row.get("amount") or "").strip()
        amount, amount_logs = _parse_amount(raw_amount, row_num)
        cleaning_log.extend(amount_logs)

        # ── Currency ─────────────────────────────────────────────────────────
        raw_currency = (row.get("currency") or "").strip()
        currency = raw_currency.upper()
        if currency != raw_currency:
            cleaning_log.append({"field": "currency", "from": raw_currency, "to": currency, "rule": "uppercase"})

        # ── Status ───────────────────────────────────────────────────────────
        raw_status = (row.get("status") or "").strip()
        status = raw_status.upper()
        if status != raw_status:
            cleaning_log.append({"field": "status", "from": raw_status, "to": status, "rule": "uppercase"})

        # ── Category ─────────────────────────────────────────────────────────
        raw_category = (row.get("category") or "").strip()
        category = raw_category if raw_category else "Uncategorised"
        if not raw_category:
            cleaning_log.append({"field": "category", "from": "", "to": "Uncategorised", "rule": "fill_missing"})

        # ── Account ID ────────────────────────────────────────────────────────
        account_id = (row.get("account_id") or "").strip()

        # ── Notes ─────────────────────────────────────────────────────────────
        notes = (row.get("notes") or "").strip()

        # ── Deduplication ────────────────────────────────────────────────────
        row_hash = _row_hash(txn_id, date, merchant, amount, account_id)
        if row_hash in seen_hashes:
            cleaning_log.append({
                "field": "row",
                "from": txn_id,
                "to": "DROPPED",
                "rule": "duplicate",
                "row_number": row_num,
            })
            log.info("Duplicate row dropped", txn_id=txn_id, row=row_num)
            continue  # Skip duplicate

        seen_hashes.add(row_hash)

        transactions.append(CleanedTransaction(
            txn_id=txn_id,
            date=date,
            merchant=merchant,
            amount=amount,
            currency=currency,
            status=status,
            category=category,
            account_id=account_id,
            notes=notes,
            cleaning_log=cleaning_log,
            row_number=row_num,
        ))

    log.info(
        "CSV cleaning complete",
        raw_rows=raw_row_count,
        clean_rows=len(transactions),
        duplicates_dropped=raw_row_count - len(transactions),
    )
    return transactions, raw_row_count
