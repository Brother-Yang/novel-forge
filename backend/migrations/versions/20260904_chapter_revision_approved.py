"""为 chapters 增加 revision_approved 字段(智能体高风险操作护栏用)。"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_chapter_revision_approved"
down_revision: Union[str, Sequence[str], None] = "20260904_agent_trace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "chapters" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chapters")}
    if "revision_approved" in cols:
        return
    with op.batch_alter_table("chapters") as batch:
        batch.add_column(
            sa.Column(
                "revision_approved", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "chapters" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chapters")}
    if "revision_approved" not in cols:
        return
    with op.batch_alter_table("chapters") as batch:
        batch.drop_column("revision_approved")
