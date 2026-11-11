"""新增 agent_trace_entries 表:智能体决策循环的完整执行轨迹(Observability)。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_agent_trace"
down_revision: str | Sequence[str] | None = "20260903_book_brief"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "agent_trace_entries" in inspector.get_table_names():
        return
    op.create_table(
        "agent_trace_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("generation_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "agent_trace_entries" not in inspector.get_table_names():
        return
    op.drop_table("agent_trace_entries")
