"""为 books 增加 key_elements 字段,保存从 brief 提取的用户明确要素(JSON 数组)。"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_book_key_elements"
down_revision: Union[str, Sequence[str], None] = "20260904_ids_uuid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "books" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("books")}
    if "key_elements" in cols:
        return
    with op.batch_alter_table("books") as batch:
        batch.add_column(
            sa.Column("key_elements", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "books" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("books")}
    if "key_elements" not in cols:
        return
    with op.batch_alter_table("books") as batch:
        batch.drop_column("key_elements")
