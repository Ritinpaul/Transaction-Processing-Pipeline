from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    GEMINI_API_KEY: str
    OPENROUTER_API_KEY: str

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/transactions_db"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@postgres:5432/transactions_db"

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # App
    APP_ENV: Literal["development", "production", "testing"] = "development"
    LOG_LEVEL: str = "DEBUG"

    # Upload constraints
    MAX_UPLOAD_SIZE_MB: int = 5

    # LLM settings
    BATCH_SIZE_LLM: int = 15
    LLM_MAX_RETRIES: int = 3
    LLM_RPM_LIMIT: int = 9  # Gemini free tier is 10, we throttle at 9

    # Worker
    WORKER_CONCURRENCY: int = 2


settings = Settings()
