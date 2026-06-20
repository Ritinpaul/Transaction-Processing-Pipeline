import pytest
import asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.models.job import Job
from app.api.jobs import job_repository

@pytest.mark.asyncio
async def test_full_pipeline_upload_and_status(client: AsyncClient, db_session: AsyncSession, clean_csv_bytes, mock_llm_response):
    # 1. Upload CSV
    files = {"file": ("clean_sample.csv", clean_csv_bytes, "text/csv")}
    response = await client.post("/jobs/upload", files=files)
    
    assert response.status_code == 201
    data = response.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "processing", "completed")
    
    job_id = data["job_id"]
    
    # Check DB
    db_job = await job_repository.get_job_by_id(db_session, job_id)
    assert db_job is not None
    assert db_job.status.value in ("pending", "processing", "completed")
    assert db_job.filename == "clean_sample.csv"
    
    # 3. Check status endpoint
    status_resp = await client.get(f"/jobs/{job_id}/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] in ("pending", "processing", "completed")
    
    # 4. Check audit log endpoint
    audit_resp = await client.get(f"/jobs/{job_id}/audit")
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert len(audit_data["audit_trail"]) >= 1
    assert any(event["event_type"] == "status_change" and event["new_status"] == "pending" for event in audit_data["audit_trail"])
