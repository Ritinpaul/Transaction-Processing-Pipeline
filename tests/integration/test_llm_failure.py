import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import uuid

@pytest.mark.asyncio
async def test_retry_endpoint(client: AsyncClient, db_session: AsyncSession):
    # 1. Create a fake failed job in DB manually
    job_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO jobs (id, filename, status, error_message, file_hash, created_at, updated_at) "
             "VALUES (:id, 'failed.csv', 'failed', 'Simulated failure', 'dummy_hash', NOW(), NOW())"),
        {"id": str(job_id)}
    )
    await db_session.commit()
    
    # 2. Call /retry endpoint
    resp = await client.post(f"/jobs/{job_id}/retry")
    assert resp.status_code == 202
    
    # 3. Check DB - job should be pending
    result = await db_session.execute(
        text("SELECT status, error_message FROM jobs WHERE id = :id"),
        {"id": str(job_id)}
    )
    row = result.fetchone()
    assert row[0] == "pending"
    assert row[1] is None
    
    # 4. Check Audit log for retry event
    audit_resp = await client.get(f"/jobs/{job_id}/audit")
    audit_data = audit_resp.json()
    assert any(event["event_type"] == "manual_retry" for event in audit_data["audit_trail"])

@pytest.mark.asyncio
async def test_retry_endpoint_invalid_status(client: AsyncClient, db_session: AsyncSession):
    # 1. Create a fake completed job
    job_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO jobs (id, filename, status, file_hash, created_at, updated_at) "
             "VALUES (:id, 'ok.csv', 'completed', 'dummy_hash2', NOW(), NOW())"),
        {"id": str(job_id)}
    )
    await db_session.commit()
    
    # 2. Try to retry
    resp = await client.post(f"/jobs/{job_id}/retry")
    assert resp.status_code == 409
    assert "cannot be retried" in resp.json()["detail"]
