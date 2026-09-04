"""把 volumes/chapters/characters/worldbuilding/plot_threads/generation_tasks/
agent_trace_entries/embeddings 的整数主键与外键/弱引用字段改为 VARCHAR(36),
与 books.id 保持一致的 UUID 字符串主键体系。

与 20260903_book_id_uuid 一致的策略:只转换列类型,不回填/重写成真正的
UUID(已有整数值会变成字符串形式的旧值,如 "1");新建记录由应用层
(models.py::new_id())生成真正的 UUID。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_ids_uuid"
down_revision: Union[str, Sequence[str], None] = "20260904_chapter_revision_approved"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 表名 -> [(列名, 该列是否允许为空), ...]
_TABLE_COLUMNS: dict[str, list[tuple[str, bool]]] = {
    "volumes": [("id", False)],
    "chapters": [("id", False), ("volume_id", True)],
    "chapter_content": [("chapter_id", False)],
    "chapter_summaries": [("chapter_id", False)],
    "characters": [("id", False)],
    "worldbuilding": [("id", False)],
    "plot_threads": [("id", False)],
    "generation_tasks": [("id", False), ("target_id", True)],
    "agent_trace_entries": [("id", False), ("task_id", False)],
    "embeddings": [("id", False), ("source_id", False)],
}


def _column_sql_type(table: str, column: str) -> str | None:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return None
    for col in inspector.get_columns(table):
        if col["name"] == column:
            return str(col["type"]).upper()
    return None


def _already_string(sql_type: str) -> bool:
    return any(token in sql_type for token in ("CHAR", "TEXT", "CLOB", "UUID"))


def upgrade() -> None:
    for table, columns in _TABLE_COLUMNS.items():
        pending: list[tuple[str, bool]] = []
        for column, nullable in columns:
            kind = _column_sql_type(table, column)
            if kind is None or _already_string(kind):
                continue
            pending.append((column, nullable))
        if not pending:
            continue
        with op.batch_alter_table(table) as batch:
            for column, nullable in pending:
                batch.alter_column(
                    column,
                    existing_type=sa.Integer(),
                    type_=sa.String(36),
                    existing_nullable=nullable,
                )


def downgrade() -> None:
    raise NotImplementedError("整数主键改为 UUID 字符串后不可逆")
