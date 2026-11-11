"""Track the character and thread list used to derive each chapter's facts."""

import sqlalchemy as sa
from alembic import op

revision = "20261111_fact_roster_hash"
down_revision = "20261111_chapter_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chapter_facts",
        sa.Column("roster_hash", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("chapter_facts", "roster_hash")
