"""将 books.id 与各表 book_id 从自增整数改为 VARCHAR(36)。

已有书籍的数字主键会转成字符串(如 "1"),不改写成 UUID;新建书籍由应用层生成 UUID。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_book_id_uuid"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_TABLES = (
    "volumes",
    "chapters",
    "characters",
    "worldbuilding",
    "plot_threads",
    "generation_tasks",
    "embeddings",
)


def _books_id_sql_type() -> str | None:
    inspector = sa.inspect(op.get_bind())
    if "books" not in inspector.get_table_names():
        return None
    for col in inspector.get_columns("books"):
        if col["name"] == "id":
            return str(col["type"]).upper()
    return None


def _already_string(sql_type: str) -> bool:
    return any(token in sql_type for token in ("CHAR", "TEXT", "CLOB", "UUID"))


def upgrade() -> None:
    kind = _books_id_sql_type()
    if kind is None or _already_string(kind):
        return

    with op.batch_alter_table("books") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.Integer(),
            type_=sa.String(36),
            existing_nullable=False,
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in _FK_TABLES:
        if table not in tables:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "book_id",
                existing_type=sa.Integer(),
                type_=sa.String(36),
                existing_nullable=False,
            )


def downgrade() -> None:
    raise NotImplementedError("book id 从 UUID 退回整数会丢失新建书籍的主键")
