"""Observability 模块测试:record_step 落库与 list_trace 查询顺序/裁剪。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents import observability
from app.db_base import Base


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/observability_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _task(db: Session) -> models.GenerationTask:
    book = models.Book(title="测试之书", genre="玄幻", tone="热血", target_words=10000)
    db.add(book)
    db.commit()
    db.refresh(book)
    task = models.GenerationTask(book_id=book.id, type="agent", status="running")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_record_step_persists_all_fields(db):
    task = _task(db)

    observability.record_step(
        db, task.id, 1, "decision", "write_chapter({})", tool_name="write_chapter"
    )

    rows = observability.list_trace(db, task.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.task_id == task.id
    assert row.step_index == 1
    assert row.kind == "decision"
    assert row.tool_name == "write_chapter"
    assert row.payload == "write_chapter({})"


def test_record_step_tool_name_optional(db):
    task = _task(db)

    observability.record_step(db, task.id, 1, "thought", "我在思考")

    rows = observability.list_trace(db, task.id)
    assert rows[0].tool_name is None


def test_list_trace_orders_by_insertion_and_scopes_to_task(db):
    task_a = _task(db)
    task_b = _task(db)

    observability.record_step(db, task_a.id, 1, "log", "a-1")
    observability.record_step(db, task_b.id, 1, "log", "b-1")
    observability.record_step(db, task_a.id, 2, "log", "a-2")

    rows_a = observability.list_trace(db, task_a.id)
    assert [r.payload for r in rows_a] == ["a-1", "a-2"]

    rows_b = observability.list_trace(db, task_b.id)
    assert [r.payload for r in rows_b] == ["b-1"]


def test_list_trace_respects_limit(db):
    task = _task(db)
    for i in range(5):
        observability.record_step(db, task.id, i, "log", f"entry-{i}")

    rows = observability.list_trace(db, task.id, limit=2)
    assert [r.payload for r in rows] == ["entry-0", "entry-1"]
