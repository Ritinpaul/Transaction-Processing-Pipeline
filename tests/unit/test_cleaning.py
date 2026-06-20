import pytest
from decimal import Decimal
from app.services.cleaning import clean_csv, CleanedTransaction

def test_clean_csv_clean_sample(clean_csv_bytes):
    transactions, raw_count = clean_csv(clean_csv_bytes)
    assert len(transactions) == 5
    
    # Check first transaction
    t1 = transactions[0]
    assert t1.txn_id == "TXN001"
    assert t1.date == "2024-01-01"
    assert t1.amount == Decimal("150.00")
    assert t1.currency == "INR"
    assert t1.status == "SUCCESS"
    assert t1.category == "Shopping"
    assert t1.account_id == "ACC001"

def test_clean_csv_dirty_sample(dirty_csv_bytes):
    transactions, raw_count = clean_csv(dirty_csv_bytes)
    
    # Dirty sample has 5 rows:
    # 1. TXN001 (Valid)
    # 2. TXN002 (Valid, date parsed, USD)
    # 3. TXN001 (Duplicate -> should be dropped)
    # 4. Missing txn_id (Should be ORPHAN_x)
    # 5. TXN005 (Valid)
    # Total expected: 4
    assert len(transactions) == 4
    
    # TXN001 rules check
    t1 = next(t for t in transactions if t.txn_id == "TXN001")
    assert t1.date == "2024-01-01"  # "01-01-2024" converted
    assert t1.amount == Decimal("150.00")  # "$150.00" converted
    assert t1.currency == "INR"  # "inr" converted
    assert t1.status == "SUCCESS"  # "success" converted
    assert t1.category == "Uncategorised"  # blank converted
    
    # Check cleaning log
    assert any(log["field"] == "date" for log in t1.cleaning_log)
    assert any(log["field"] == "amount" for log in t1.cleaning_log)
    assert any(log["field"] == "currency" for log in t1.cleaning_log)
    
    # TXN002
    t2 = next(t for t in transactions if t.txn_id == "TXN002")
    assert t2.date == "2024-01-02" # "2024/01/02" converted
    
    # Missing txn_id
    orphan = next(t for t in transactions if t.txn_id.startswith("ORPHAN_"))
    assert orphan.txn_id == "ORPHAN_5"
    assert orphan.merchant == "Ola"
    assert orphan.status == "FAILED"
