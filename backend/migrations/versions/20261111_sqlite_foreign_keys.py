"""Repair orphaned legacy data and enforce the declared delete lifecycle."""

import logging

import sqlalchemy as sa
from alembic import op

revision = "20261111_sqlite_foreign_keys"
down_revision = "20260904_book_key_elements"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# All relationships that are enforced by the application model. A chapter's
# volume only groups chapters, so deleting its volume must retain its content.
_RELATIONS = {
    "volumes": [("book_id", "books", "CASCADE")],
    "chapters": [("book_id", "books", "CASCADE"), ("volume_id", "volumes", "SET NULL")],
    "chapter_content": [("chapter_id", "chapters", "CASCADE")],
    "chapter_summaries": [("chapter_id", "chapters", "CASCADE")],
    "characters": [("book_id", "books", "CASCADE")],
    "worldbuilding": [("book_id", "books", "CASCADE")],
    "plot_threads": [("book_id", "books", "CASCADE")],
    "generation_tasks": [("book_id", "books", "CASCADE")],
    "agent_trace_entries": [("task_id", "generation_tasks", "CASCADE")],
    "embeddings": [("book_id", "books", "CASCADE")],
}


def upgrade() -> None:
    bind = op.get_bind()
    for table, relationships in _RELATIONS.items():
        for column, parent, action in relationships:
            if action == "SET NULL":
                sql = f'UPDATE "{table}" SET "{column}" = NULL WHERE "{column}" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "{parent}" WHERE "{parent}".id = "{table}"."{column}")'
            else:
                sql = f'DELETE FROM "{table}" WHERE NOT EXISTS (SELECT 1 FROM "{parent}" WHERE "{parent}".id = "{table}"."{column}")'
            result = bind.execute(sa.text(sql))
            if result.rowcount:
                logger.warning(
                    "Repaired %s orphaned rows in %s.%s", result.rowcount, table, column
                )

    # Foreign keys alone cannot express that a volume and chapter share a book.
    # Detach invalid legacy grouping while preserving the chapter and its body.
    result = bind.execute(sa.text(
        "UPDATE chapters SET volume_id = NULL WHERE EXISTS ("
        "SELECT 1 FROM volumes WHERE volumes.id = chapters.volume_id "
        "AND volumes.book_id != chapters.book_id)"
    ))
    if result.rowcount:
        logger.warning("Detached %s cross-book chapter associations", result.rowcount)

    # A legacy foreign key may exist without its ON DELETE action, or may be
    # entirely absent. Rebuild only those constraints, preserving table data.
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    for table, relationships in _RELATIONS.items():
        foreign_keys = sa.inspect(bind).get_foreign_keys(table)
        pending: list[tuple[str, str, str, str | None]] = []
        for column, parent, action in relationships:
            current = next(
                (fk for fk in foreign_keys if fk["constrained_columns"] == [column]),
                None,
            )
            if (
                current
                and current["referred_table"] == parent
                and current["referred_columns"] == ["id"]
                and current.get("options", {}).get("ondelete", "").upper() == action
            ):
                continue
            name = (
                None
                if current is None
                else current["name"]
                or f"fk_{table}_{column}_{current['referred_table']}"
            )
            pending.append((column, parent, action, name))
        if pending:
            with op.batch_alter_table(table, naming_convention=naming) as batch:
                for column, parent, action, old_name in pending:
                    if old_name:
                        batch.drop_constraint(old_name, type_="foreignkey")
                    batch.create_foreign_key(
                        f"fk_{table}_{column}_{parent}",
                        parent,
                        [column],
                        ["id"],
                        ondelete=action,
                    )


def downgrade() -> None:
    # Removing constraints does not restore orphan rows; keep the integrity fix.
    raise NotImplementedError(
        "Orphan cleanup and foreign-key enforcement are irreversible"
    )
