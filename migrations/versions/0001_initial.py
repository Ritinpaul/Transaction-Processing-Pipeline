"""Create all tables: jobs, transactions, job_summaries, audit_logs

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-18
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── jobs ──────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="job_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("row_count_raw", sa.Integer(), nullable=True),
        sa.Column("row_count_clean", sa.Integer(), nullable=True),
        sa.Column("anomaly_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("llm_calls_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_calls_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_unique_constraint("uq_jobs_file_hash", "jobs", ["file_hash"])

    # ── transactions ──────────────────────────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("txn_id", sa.String(50), nullable=True),
        sa.Column("date", sa.String(20), nullable=True),
        sa.Column("merchant", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("account_id", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_anomaly", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("anomaly_reason", sa.Text(), nullable=True),
        sa.Column("llm_category", sa.String(100), nullable=True),
        sa.Column("llm_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("llm_prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("llm_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("llm_failed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cleaning_log", JSONB(), nullable=False, server_default="[]"),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transactions_job_id", "transactions", ["job_id"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_is_anomaly", "transactions", ["is_anomaly"])

    # ── job_summaries ─────────────────────────────────────────────────────
    op.create_table(
        "job_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_spend_inr", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_spend_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("anomaly_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transaction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_merchants", JSONB(), nullable=True),
        sa.Column("category_breakdown", JSONB(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("llm_raw_response", JSONB(), nullable=True),
        sa.Column("generation_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("llm_prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("llm_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_job_summaries_job_id", "job_summaries", ["job_id"])
    op.create_unique_constraint("uq_job_summaries_job_id", "job_summaries", ["job_id"])

    # ── audit_logs ────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("old_status", sa.String(30), nullable=True),
        sa.Column("new_status", sa.String(30), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_job_id", "audit_logs", ["job_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("job_summaries")
    op.drop_table("transactions")
    op.drop_table("jobs")
    op.execute("DROP TYPE IF EXISTS job_status_enum")
