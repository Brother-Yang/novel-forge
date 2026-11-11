"""Persist memory outbox and bind chapter summaries to their source revision."""

import sqlalchemy as sa
from alembic import op

revision = "20261111_memory_sync"
down_revision = "20261111_sqlite_foreign_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "volumes",
        sa.Column("memory_summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "chapter_summaries",
        sa.Column("source_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_table(
        "memory_sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "book_id", "source_type", "source_id", name="uq_memory_source"
        ),
    )
    op.create_index("ix_memory_sync_jobs_book_id", "memory_sync_jobs", ["book_id"])
    op.create_index("ix_memory_sync_jobs_status", "memory_sync_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("memory_sync_jobs")
    op.drop_column("chapter_summaries", "source_hash")
    op.drop_column("volumes", "memory_summary")
