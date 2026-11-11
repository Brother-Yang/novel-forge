"""Store chapter-scoped character and plot facts for time-aware replay."""

import sqlalchemy as sa
from alembic import op

revision = "20261111_chapter_facts"
down_revision = "20261111_trace_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapter_facts",
        sa.Column(
            "chapter_id",
            sa.String(36),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chapter_facts")
