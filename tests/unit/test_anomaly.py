import pytest
from decimal import Decimal
from app.services.anomaly import detect_anomalies
from app.services.cleaning import CleanedTransaction

def create_txn(txn_id, amount, account_id, merchant="Amazon", currency="INR", notes="") -> CleanedTransaction:
    return CleanedTransaction(
        txn_id=txn_id,
        date="2024-01-01",
        merchant=merchant,
        amount=Decimal(str(amount)),
        currency=currency,
        status="SUCCESS",
        category="Uncategorised",
        account_id=account_id,
        notes=notes,
        is_anomaly=False
    )

def test_anomaly_rule_1_median():
    txns = [
        create_txn("1", 100, "A1"),
        create_txn("2", 100, "A1"),
        create_txn("3", 100, "A1"),
        create_txn("4", 1000, "A1"), # 1000 > 3 * median(100)
        create_txn("5", 50, "A2"),
    ]
    result = detect_anomalies(txns)
    
    assert result[0].is_anomaly is False
    assert result[3].is_anomaly is True
    assert "Amount exceeds 3x account median" in result[3].anomaly_reason
    assert result[4].is_anomaly is False

def test_anomaly_rule_2_merchant_currency():
    txns = [
        create_txn("1", 100, "A1", merchant="Zomato", currency="USD"), # Domestic merchant + USD
        create_txn("2", 100, "A1", merchant="Amazon", currency="USD"), # Non-domestic + USD is fine
        create_txn("3", 100, "A1", merchant="Zomato", currency="INR"), # Domestic + INR is fine
    ]
    result = detect_anomalies(txns)
    
    assert result[0].is_anomaly is True
    assert "USD transaction at domestic-only merchant" in result[0].anomaly_reason
    
    assert result[1].is_anomaly is False
    assert result[2].is_anomaly is False

def test_anomaly_rule_3_notes_suspicious():
    txns = [
        create_txn("1", 100, "A1", notes="This is SUSPICIOUS activity"),
        create_txn("2", 100, "A1", notes="All good"),
    ]
    result = detect_anomalies(txns)
    
    assert result[0].is_anomaly is True
    assert "Flagged as suspicious in source data notes" in result[0].anomaly_reason
    assert result[1].is_anomaly is False
