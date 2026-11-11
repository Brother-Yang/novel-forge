"""为 books 增加 brief 字段,保存一句话出书的原始输入。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_book_brief"
down_revision: str | Sequence[str] | None = "20260903_book_id_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "books" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("books")}
    if "brief" in cols:
        return
    with op.batch_alter_table("books") as batch:
        batch.add_column(
            sa.Column("brief", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "books" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("books")}
    if "brief" not in cols:
        return
    with op.batch_alter_table("books") as batch:
        batch.drop_column("brief")
