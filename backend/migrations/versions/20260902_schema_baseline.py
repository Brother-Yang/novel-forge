"""Create the complete schema without relying on application create_all.

Existing tables are deliberately left intact: historical migrations below this
baseline upgrade their columns and preserve their primary keys and data.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260902_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _id(name: str = "id", *, primary_key: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(36), primary_key=primary_key, nullable=False)


def _book_id() -> sa.Column:
    return sa.Column(
        "book_id",
        sa.String(36),
        sa.ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )


def _text(name: str, default: str = "") -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=False, server_default=default)


def _integer(name: str, default: str = "0") -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=default)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name: str, *columns: sa.Column) -> None:
        if name not in existing:
            op.create_table(name, *columns)

    create(
        "books",
        _id(),
        sa.Column("title", sa.String(255), nullable=False),
        _text("intro"),
        _text("brief"),
        sa.Column("genre", sa.String(128), nullable=False, server_default=""),
        sa.Column("tone", sa.String(128), nullable=False, server_default=""),
        _integer("target_words", "100000"),
        _text("key_elements", "[]"),
        _created_at(),
    )
    create(
        "volumes",
        _id(),
        _book_id(),
        sa.Column("title", sa.String(255), nullable=False),
        _integer("order_index"),
        _text("summary"),
    )
    create(
        "chapters",
        _id(),
        _book_id(),
        sa.Column(
            "volume_id", sa.String(36), sa.ForeignKey("volumes.id", ondelete="SET NULL")
        ),
        _integer("order_index"),
        sa.Column("title", sa.String(255), nullable=False),
        _text("outline"),
        sa.Column("status", sa.String(32), nullable=False, server_default="empty"),
        _integer("word_count"),
        sa.Column(
            "revision_approved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    for table, body in (("chapter_content", "body"), ("chapter_summaries", "summary")):
        create(
            table,
            sa.Column(
                "chapter_id",
                sa.String(36),
                sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            _text(body),
        )
    create(
        "characters",
        _id(),
        _book_id(),
        sa.Column("name", sa.String(128), nullable=False),
        _integer("order_index"),
        _text("md_content"),
        _text("state_json", "{}"),
    )
    create(
        "worldbuilding",
        _id(),
        _book_id(),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("title", sa.String(255), nullable=False),
        _text("md_content"),
    )
    create(
        "plot_threads",
        _id(),
        _book_id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        _text("description"),
    )
    create(
        "generation_tasks",
        _id(),
        _book_id(),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        _integer("progress"),
        _text("error"),
        _text("checkpoint_json", "{}"),
        _created_at(),
    )
    create(
        "agent_trace_entries",
        _id(),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _integer("step_index"),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(128)),
        _text("payload"),
        _created_at(),
    )
    create(
        "embeddings",
        _id(),
        _book_id(),
        sa.Column("source_type", sa.String(32), nullable=False),
        _id("source_id", primary_key=False),
        sa.Column("chroma_id", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "The schema baseline cannot be removed without losing manuscripts"
    )
