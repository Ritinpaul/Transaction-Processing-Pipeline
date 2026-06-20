import pytest
import pytest_asyncio
import json
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch

from app.main import app
from app.core.database import engine, get_db
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURES_DIR = Path(__file__).parent / "fixtures"

import asyncio
import json

@pytest.fixture(autouse=True)
def bypass_rate_limit(monkeypatch):
    from app.core.middleware import UploadsRateLimitMiddleware
    monkeypatch.setattr(UploadsRateLimitMiddleware, "MAX_UPLOADS_PER_MINUTE", 10000)

@pytest.fixture
def clean_csv_bytes():
    with open(FIXTURES_DIR / "clean_sample.csv", "rb") as f:
        return f.read()

@pytest.fixture
def dirty_csv_bytes():
    with open(FIXTURES_DIR / "dirty_sample.csv", "rb") as f:
        return f.read()

@pytest.fixture
def mock_llm_response():
    with open(FIXTURES_DIR / "mock_llm_response.json", "r") as f:
        return json.load(f)

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.core.config import settings

from app.core.database import engine, AsyncSessionLocal
from sqlalchemy import text
import uuid

@pytest_asyncio.fixture(autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()

@pytest_asyncio.fixture()
async def db_session():
    """Provides a real database session and cleans up DB before test."""
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE transactions, jobs RESTART IDENTITY CASCADE;"))
    
    async with AsyncSessionLocal() as session:
        yield session

@pytest_asyncio.fixture()
async def client():
    """Real client without dependency override."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest.fixture
def mock_celery_delay():
    with patch("app.api.jobs.process_csv_job.delay") as mock_delay:
        yield mock_delay
