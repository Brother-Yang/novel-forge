"""Index per-book catalog reads in their stable display order."""

from alembic import op

revision = "20261111_catalog_order_indexes"
down_revision = "20261111_fact_roster_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_volumes_book_order", "volumes", ["book_id", "order_index", "id"]
    )
    op.create_index(
        "ix_chapters_book_order", "chapters", ["book_id", "order_index", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chapters_book_order", table_name="chapters")
    op.drop_index("ix_volumes_book_order", table_name="volumes")
