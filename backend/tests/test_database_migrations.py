"""Migration-only installation and legacy integrity repair on temporary databases."""

import io
import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import database, models
from app.db_base import Base


def migrate(path: Path, target: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # Deliberately do not alter app.settings: administrative migration URLs
    # must not accidentally be redirected to the configured business database.
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, target)


def test_empty_database_is_created_by_migrations_only(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    migrate(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        for name, table in Base.metadata.tables.items():
            actual = {column["name"]: column for column in inspector.get_columns(name)}
            assert set(actual) == set(table.columns.keys())
            for column in table.columns:
                assert str(actual[column.name]["type"]) == str(column.type)
                assert actual[column.name]["nullable"] == column.nullable
        with Session(engine) as db:
            book = models.Book(title="仅迁移建库")
            chapter = models.Chapter(
                book=book, title="章节", content=models.ChapterContent(body="正文")
            )
            db.add(book)
            db.commit()
            content = db.get(models.ChapterContent, chapter.id)
            assert content is not None and content.body == "正文"
        migrate(path)  # Restart is idempotent and retains data.
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT title FROM books")).scalar_one()
                == "仅迁移建库"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "stamped", [False, True], ids=["unversioned", "already-versioned"]
)
def test_legacy_orphans_are_repaired_and_delete_actions_are_enforced(
    tmp_path: Path, stamped: bool
) -> None:
    path = tmp_path / "orphans.db"
    migrate(path, "20260904_book_key_elements")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            if not stamped:
                connection.execute(text("DELETE FROM alembic_version"))
            statements = [
                "INSERT INTO books (id,title) VALUES ('keep','保留书稿')",
                "INSERT INTO books (id,title) VALUES ('remove','待删除书稿')",
                "INSERT INTO volumes (id,book_id,title) VALUES ('volume','keep','卷')",
                "INSERT INTO volumes (id,book_id,title) VALUES ('orphan-volume','missing','孤儿卷')",
                "INSERT INTO chapters (id,book_id,volume_id,title) VALUES ('chapter','keep','orphan-volume','保留正文')",
                "INSERT INTO chapters (id,book_id,volume_id,title) VALUES ('loose','keep','missing-volume','保留散章')",
                "INSERT INTO chapters (id,book_id,title) VALUES ('orphan-chapter','missing','孤儿章')",
                "INSERT INTO chapter_content (chapter_id,body) VALUES ('chapter','不能丢失')",
                "INSERT INTO chapter_content (chapter_id,body) VALUES ('orphan-chapter','孤儿正文')",
                "INSERT INTO chapter_summaries (chapter_id,summary) VALUES ('absent-chapter','孤儿摘要')",
                "INSERT INTO characters (id,book_id,name) VALUES ('character','missing','孤儿人物')",
                "INSERT INTO worldbuilding (id,book_id,title) VALUES ('world','missing','孤儿设定')",
                "INSERT INTO plot_threads (id,book_id,name) VALUES ('plot','missing','孤儿线程')",
                "INSERT INTO generation_tasks (id,book_id,type) VALUES ('orphan-task','missing','book')",
                "INSERT INTO agent_trace_entries (id,task_id,kind) VALUES ('trace','orphan-task','log')",
                "INSERT INTO embeddings (id,book_id,source_type,source_id,chroma_id) VALUES ('embedding','missing','chapter_summary','absent','vector')",
            ]
            for statement in statements:
                connection.execute(text(statement))
        migrate(path)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert (
                connection.execute(
                    text("SELECT body FROM chapter_content")
                ).scalar_one()
                == "不能丢失"
            )
            assert connection.execute(
                text("SELECT volume_id FROM chapters ORDER BY id")
            ).all() == [(None,), (None,)]
            for table in (
                "characters",
                "worldbuilding",
                "plot_threads",
                "generation_tasks",
                "agent_trace_entries",
                "embeddings",
                "chapter_summaries",
            ):
                assert (
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar_one()
                    == 0
                )
            assert (
                connection.execute(text("SELECT COUNT(*) FROM volumes")).scalar_one()
                == 1
            )
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(text("DELETE FROM books WHERE id='keep'"))
            assert (
                connection.execute(text("SELECT COUNT(*) FROM chapters")).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM chapter_content")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT title FROM books")).scalar_one()
                == "待删除书稿"
            )
    finally:
        engine.dispose()


def test_legacy_cross_book_volume_is_detached_without_losing_body(tmp_path: Path) -> None:
    path = tmp_path / "cross-book.db"
    migrate(path, "20260904_book_key_elements")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            for statement in (
                "INSERT INTO books (id,title) VALUES ('a','本书'),('b','他书')",
                "INSERT INTO volumes (id,book_id,title) VALUES ('v','b','他书卷')",
                "INSERT INTO chapters (id,book_id,volume_id,title) VALUES ('c','a','v','本书章')",
                "INSERT INTO chapter_content (chapter_id,body) VALUES ('c','保留原稿')",
            ):
                connection.execute(text(statement))
        migrate(path)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT book_id,volume_id FROM chapters")).one() == ("a", None)
            assert connection.execute(text("SELECT body FROM chapter_content")).scalar_one() == "保留原稿"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_legacy_foreign_keys_without_cascade_are_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql(
                "CREATE TABLE books (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL, intro TEXT, genre VARCHAR(128), tone VARCHAR(128), target_words INTEGER, created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE volumes (id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id), title VARCHAR(255) NOT NULL, order_index INTEGER, summary TEXT)"
            )
            connection.exec_driver_sql(
                "INSERT INTO books VALUES (1,'旧书稿','','','',100000,CURRENT_TIMESTAMP)"
            )
            connection.exec_driver_sql(
                "INSERT INTO volumes VALUES (2,1,'旧卷',0,'旧摘要')"
            )
        migrate(path)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("SELECT id, title FROM books").one() == (
                "1",
                "旧书稿",
            )
            assert connection.exec_driver_sql(
                "SELECT id, book_id, summary FROM volumes"
            ).one() == ("2", "1", "旧摘要")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql("DELETE FROM books WHERE id='1'")
            assert (
                connection.exec_driver_sql("SELECT COUNT(*) FROM volumes").scalar_one()
                == 0
            )
        assert (
            inspect(engine)
            .get_foreign_keys("volumes")[0]
            .get("options", {})
            .get("ondelete")
            == "CASCADE"
        )
    finally:
        engine.dispose()


def test_all_application_connections_enforce_foreign_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import event

    # Verify registration without opening the configured business database.
    assert event.contains(
        database.engine, "connect", database._enable_sqlite_foreign_keys
    )
    isolated = create_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    event.listen(isolated, "connect", database._enable_sqlite_foreign_keys)
    monkeypatch.setattr(database, "engine", isolated)
    connections = [database.engine.connect() for _ in range(3)]
    try:
        for connection in connections:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        for connection in connections:
            connection.close()
    database.engine.dispose()
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_init_db_does_not_call_create_all(monkeypatch: pytest.MonkeyPatch) -> None:
    upgraded: list[bool] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Schema must be owned by Alembic")

    monkeypatch.setattr(Base.metadata, "create_all", forbidden)
    monkeypatch.setattr(database, "_run_alembic_upgrade", lambda: upgraded.append(True))
    database.init_db()
    assert upgraded == [True]


def test_migrations_do_not_silence_existing_application_loggers(tmp_path: Path) -> None:
    logger = logging.getLogger("app.memory.migration_logging_test")
    logger.disabled = False
    migrate(tmp_path / "logging.db")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger.addHandler(handler)
    try:
        logger.warning("记忆同步失败仍可定位")
        assert "记忆同步失败仍可定位" in output.getvalue()
    finally:
        logger.removeHandler(handler)


def test_application_connection_rejects_orphan_insert(tmp_path: Path) -> None:
    from sqlalchemy import event

    path = tmp_path / "fk.db"
    migrate(path)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", database._enable_sqlite_foreign_keys)
    try:
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO volumes (id,book_id,title) VALUES ('v','missing','不允许')"
                )
            )
    finally:
        engine.dispose()


def test_full_integer_legacy_schema_preserves_body_summary_and_task_history(
    tmp_path: Path,
) -> None:
    import sqlalchemy as sa

    path = tmp_path / "full-legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    legacy = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        copied = table.to_metadata(legacy)
        for column in copied.columns:
            if isinstance(column.type, sa.String) and column.type.length == 36:
                column.type = sa.Integer()
    legacy.create_all(engine)
    try:
        with Session(engine) as db:
            book = models.Book(id="1", title="旧版完整书稿")
            volume = models.Volume(id="2", book=book, title="旧卷", summary="卷摘要")
            chapter = models.Chapter(
                id="3",
                book=book,
                volume=volume,
                title="旧章",
                status="done",
                content=models.ChapterContent(body="不能被批量重建级联删除的正文"),
                summary=models.ChapterSummary(summary="旧章摘要"),
            )
            db.add_all([book, volume, chapter])
            db.flush()
            task = models.GenerationTask(
                id="4", book_id="1", type="book", status="done"
            )
            db.add(task)
            db.flush()
            db.add_all(
                [
                    models.AgentTraceEntry(
                        id="5", task_id="4", sequence=1, kind="log", payload="旧轨迹"
                    ),
                    models.Worldbuilding(
                        id="6", book_id="1", title="旧设定", md_content="设定正文"
                    ),
                    models.Character(id="7", book_id="1", name="旧人物"),
                    models.PlotThread(id="8", book_id="1", name="旧线程"),
                    models.EmbeddingRef(
                        id="9",
                        book_id="1",
                        source_type="chapter_summary",
                        source_id="3",
                        chroma_id="old-vector",
                    ),
                ]
            )
            db.commit()
        with engine.begin() as connection:
            # This fixture is seeded through today's ORM. Recreate the actual
            # legacy shape before testing the migration from integer IDs.
            connection.exec_driver_sql("DROP TABLE memory_sync_jobs")
            connection.exec_driver_sql("DROP TABLE chapter_facts")
            connection.exec_driver_sql("ALTER TABLE volumes DROP COLUMN memory_summary")
            connection.exec_driver_sql(
                "ALTER TABLE chapter_summaries DROP COLUMN source_hash"
            )
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("agent_trace_entries") as batch:
                batch.drop_constraint("uq_agent_trace_sequence", type_="unique")
                batch.drop_column("sequence")
            connection.exec_driver_sql(
                "ALTER TABLE generation_tasks DROP COLUMN trace_sequence"
            )
        migrate(path)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            for table in Base.metadata.tables.values():
                if table.name in {"memory_sync_jobs", "chapter_facts"}:
                    assert (
                        connection.execute(
                            sa.select(sa.func.count()).select_from(table)
                        ).scalar_one()
                        == 0
                    )
                    continue
                assert (
                    connection.execute(
                        sa.select(sa.func.count()).select_from(table)
                    ).scalar_one()
                    == 1
                )
                if "id" in table.c:
                    assert (
                        connection.execute(
                            sa.select(sa.func.typeof(table.c.id))
                        ).scalar_one()
                        == "text"
                    )
            assert (
                connection.exec_driver_sql(
                    "SELECT body FROM chapter_content"
                ).scalar_one()
                == "不能被批量重建级联删除的正文"
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT summary FROM chapter_summaries"
                ).scalar_one()
                == "旧章摘要"
            )
            assert connection.exec_driver_sql(
                "SELECT task_id, payload FROM agent_trace_entries"
            ).one() == ("4", "旧轨迹")
            assert connection.exec_driver_sql(
                "SELECT source_id, chroma_id FROM embeddings"
            ).one() == ("3", "old-vector")
    finally:
        engine.dispose()


def test_trace_sequence_backfills_same_timestamp_and_repeated_step(
    tmp_path: Path,
) -> None:
    from app.agents import observability

    path = tmp_path / "trace-legacy.db"
    migrate(path, "20261111_memory_sync")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO books (id, title) VALUES ('book', '书')"
            )
            connection.exec_driver_sql(
                "INSERT INTO generation_tasks (id, book_id, type) "
                "VALUES ('task', 'book', 'agent'), ('other', 'book', 'agent')"
            )
            connection.exec_driver_sql(
                "INSERT INTO agent_trace_entries "
                "(id, task_id, step_index, kind, payload, created_at) VALUES "
                "('z', 'task', 2, 'log', '初次生成', '2026-01-01 00:00:00'), "
                "('c', 'other', 1, 'log', '另一任务', '2026-01-01 00:00:00'), "
                "('a', 'task', 1, 'log', '重试生成', '2026-01-01 00:00:00')"
            )
        migrate(path)
        with Session(engine) as db:
            observability.record_step(db, "task", 1, "log", "迁移后新增")
            task_entries = observability.list_trace(db, "task")
            assert [(row.sequence, row.payload) for row in task_entries] == [
                (1, "初次生成"),
                (2, "重试生成"),
                (3, "迁移后新增"),
            ]
            other_entries = observability.list_trace(db, "other")
            assert [(row.sequence, row.payload) for row in other_entries] == [
                (1, "另一任务")
            ]
    finally:
        engine.dispose()
