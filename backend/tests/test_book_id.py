"""新建书籍使用 UUID 字符串主键,而不是 1、2、3 这种自增整数。"""

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db_base import Base


def test_new_book_id_is_uuid4() -> None:
    value = models.new_book_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 4
    assert models.new_book_id() != value


def test_inserting_book_assigns_distinct_uuids(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = models.Book(title="甲")
        second = models.Book(title="乙")
        db.add_all([first, second])
        db.commit()
        db.refresh(first)
        db.refresh(second)

    uuid.UUID(first.id)
    uuid.UUID(second.id)
    assert first.id != second.id
    assert first.id not in {"1", "2"}
    assert second.id not in {"1", "2"}


def test_alembic_keeps_existing_integer_ids_as_strings(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    intro TEXT,
                    genre VARCHAR(128),
                    tone VARCHAR(128),
                    target_words INTEGER,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE volumes (
                    id INTEGER PRIMARY KEY,
                    book_id INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    order_index INTEGER,
                    summary TEXT,
                    FOREIGN KEY(book_id) REFERENCES books(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO books (id, title, intro, genre, tone, target_words) "
                "VALUES (1, '旧书', '', '', '', 100000)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO volumes (id, book_id, title, order_index, summary) "
                "VALUES (1, 1, '第一卷', 0, '')"
            )
        )

    # 与 database.py::init_db() 的真实顺序一致:先 create_all 补齐缺失的表
    # (已存在的 books/volumes 不受影响),再跑 Alembic 增量补丁,避免后续新表
    # (如 agent_trace_entries)里指向 generation_tasks 的外键在合成的"纯旧库"
    # 场景下悬空引用一张从未创建过的表。
    Base.metadata.create_all(engine)

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        book_id, title = conn.execute(text("SELECT id, title FROM books")).one()
        volume_book_id = conn.execute(text("SELECT book_id FROM volumes")).scalar_one()
        id_type = conn.execute(text("SELECT typeof(id) FROM books")).scalar_one()
        brief = conn.execute(text("SELECT brief FROM books")).scalar_one()

    assert title == "旧书"
    assert str(book_id) == "1"
    assert str(volume_book_id) == "1"
    assert id_type == "text"
    assert brief == ""
