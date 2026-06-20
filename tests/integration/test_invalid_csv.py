import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_upload_invalid_file_type(client: AsyncClient):
    files = {"file": ("test.txt", b"just some text", "text/plain")}
    resp = await client.post("/jobs/upload", files=files)
    assert resp.status_code == 400
    assert "Invalid file type" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_upload_empty_csv(client: AsyncClient):
    files = {"file": ("empty.csv", b"", "text/csv")}
    resp = await client.post("/jobs/upload", files=files)
    assert resp.status_code in (400, 422)

@pytest.mark.asyncio
async def test_upload_missing_columns(client: AsyncClient):
    bad_csv = b"txn_id,date,amount\n1,2024-01-01,100"
    files = {"file": ("bad.csv", bad_csv, "text/csv")}
    resp = await client.post("/jobs/upload", files=files)
    assert resp.status_code in (400, 422)
