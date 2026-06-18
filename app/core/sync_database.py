"""
Synchronous SQLAlchemy engine — for use in Celery tasks.
Celery workers run in a sync context, not async, so we need
a separate synchronous engine backed by psycopg2.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings

sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,  # Don't log every SQL in worker context
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
)


def get_sync_db() -> Session:
    """Return a new synchronous DB session. Caller must close it."""
    return SyncSessionLocal()
