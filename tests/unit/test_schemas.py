import pytest
from pydantic import ValidationError
from app.schemas.job import JobResponse, JobStatus

def test_job_response_schema():
    job_data = {
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "pending",
        "filename": "test.csv",
        "created_at": "2024-01-01T00:00:00Z"
    }
    
    # Valid
    response = JobResponse(**job_data)
    assert response.status == JobStatus.PENDING
    assert response.filename == "test.csv"

def test_job_response_invalid_status():
    job_data = {
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "invalid_status",
        "filename": "test.csv",
        "created_at": "2024-01-01T00:00:00Z"
    }
    
    with pytest.raises(ValidationError):
        JobResponse(**job_data)
