"""Assign durable per-task cursors to existing and future agent trace entries."""

import sqlalchemy as sa
from alembic import op

revision = "20261111_trace_sequence"
down_revision = "20261111_memory_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column("trace_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_trace_entries", sa.Column("sequence", sa.Integer()))
    # On SQLite, legacy trace rows retain insertion order in rowid even when a
    # resumed task repeats a step within the same timestamp.
    legacy_order = (
        "rowid"
        if op.get_bind().dialect.name == "sqlite"
        else "created_at, step_index, id"
    )
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY task_id ORDER BY {legacy_order}
            ) AS position
            FROM agent_trace_entries
        )
        UPDATE agent_trace_entries
        SET sequence = ranked.position
        FROM ranked
        WHERE agent_trace_entries.id = ranked.id
        """
    )
    with op.batch_alter_table("agent_trace_entries") as batch:
        batch.alter_column("sequence", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint(
            "uq_agent_trace_sequence", ["task_id", "sequence"]
        )
    op.execute(
        """
        UPDATE generation_tasks
        SET trace_sequence = COALESCE((
            SELECT MAX(sequence)
            FROM agent_trace_entries
            WHERE task_id = generation_tasks.id
        ), 0)
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_trace_entries") as batch:
        batch.drop_constraint("uq_agent_trace_sequence", type_="unique")
        batch.drop_column("sequence")
    op.drop_column("generation_tasks", "trace_sequence")
