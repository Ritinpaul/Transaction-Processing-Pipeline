import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.job import Job
from sqlalchemy import select

@pytest.mark.asyncio
async def test_upload_idempotency(client: AsyncClient, db_session: AsyncSession, clean_csv_bytes, mock_celery_delay):
    # 1. First upload
    files1 = {"file": ("clean_sample.csv", clean_csv_bytes, "text/csv")}
    resp1 = await client.post("/jobs/upload", files=files1)
    assert resp1.status_code == 201
    job_id_1 = resp1.json()["job_id"]
    assert not resp1.json().get("is_duplicate", False)
    
    # 2. Second upload (exact same file)
    files2 = {"file": ("clean_sample.csv", clean_csv_bytes, "text/csv")}
    resp2 = await client.post("/jobs/upload", files=files2)
    assert resp2.status_code == 200 # Important: 200 OK for duplicate, not 202
    job_id_2 = resp2.json()["job_id"]
    assert resp2.json().get("is_duplicate", True)
    
    # Assert same job ID returned
    assert job_id_1 == job_id_2
    
    # Assert DB has exactly 1 job
    result = await db_session.execute(select(Job))
    jobs = result.scalars().all()
    assert len(jobs) == 1
