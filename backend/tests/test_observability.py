"""Observability 模块测试:record_step 落库与 list_trace 查询顺序/裁剪。"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents import observability
from app.database import get_db
from app.db_base import Base
from app.main import create_app


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/observability_test.db")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


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


def test_same_task_writers_commit_unique_sequences(db: Session) -> None:
    task_id = _task(db).id
    bind = db.get_bind()

    def record(index: int) -> None:
        with Session(bind) as writer:
            observability.record_step(writer, task_id, 1, "log", f"entry-{index}")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(record, range(24)))

    rows = observability.list_trace(db, task_id)
    assert [row.sequence for row in rows] == list(range(1, 25))
    assert {row.payload for row in rows} == {f"entry-{index}" for index in range(24)}


async def test_trace_http_pages_through_retries_and_new_events(db: Session) -> None:
    task_id = _task(db).id
    other_id = _task(db).id
    bind = db.get_bind()

    def override_db() -> Iterator[Session]:
        with Session(bind) as session:
            yield session

    app = create_app()
    app.state.runtime_ready = True
    app.dependency_overrides[get_db] = override_db
    url = f"/api/tasks/{task_id}/trace/page"

    for index in range(1, 6):
        observability.record_step(db, task_id, 1, "log", f"第{index}条")
    observability.record_step(db, other_id, 1, "log", "另一个任务")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        latest = await client.get(url, params={"limit": 2})
        assert latest.status_code == 200
        latest_page = latest.json()
        assert latest_page["has_more"] is True
        assert [entry["sequence"] for entry in latest_page["entries"]] == [4, 5]
        assert all(entry["step_index"] == 1 for entry in latest_page["entries"])
        assert {entry["task_id"] for entry in latest_page["entries"]} == {task_id}

        for index in range(6, 9):
            observability.record_step(db, task_id, 1, "log", f"第{index}条")
        newer = (await client.get(url, params={"limit": 2, "after": 5})).json()
        assert newer["has_more"] is True
        assert [entry["sequence"] for entry in newer["entries"]] == [6, 7]
        last = (await client.get(url, params={"limit": 2, "after": 7})).json()
        assert last["has_more"] is False
        assert [entry["sequence"] for entry in last["entries"]] == [8]
        assert (await client.get(url, params={"after": 8})).json() == {
            "entries": [],
            "has_more": False,
        }

        older = (await client.get(url, params={"limit": 2, "before": 4})).json()
        assert older["has_more"] is True
        assert [entry["sequence"] for entry in older["entries"]] == [2, 3]
        first = (await client.get(url, params={"limit": 2, "before": 2})).json()
        assert first["has_more"] is False
        assert [entry["sequence"] for entry in first["entries"]] == [1]

        for invalid in (
            "limit=101",
            "limit=0",
            "before=0",
            "after=-1",
            "before=2&after=0",
        ):
            assert (await client.get(f"{url}?{invalid}")).status_code == 422
        assert (await client.get("/api/tasks/unknown/trace/page")).status_code == 404
        legacy = (await client.get(f"/api/tasks/{task_id}/trace")).json()
        assert [entry["payload"] for entry in legacy] == [
            f"第{i}条" for i in range(1, 9)
        ]

        for index in range(9, 509):
            observability.record_step(db, task_id, 1, "log", f"第{index}条")
        long_run_tail = (await client.get(url)).json()
        assert long_run_tail["has_more"] is True
        assert [entry["sequence"] for entry in long_run_tail["entries"]] == list(
            range(459, 509)
        )
        beyond_legacy_limit = (await client.get(url, params={"after": 500})).json()
        assert beyond_legacy_limit["has_more"] is False
        assert [entry["sequence"] for entry in beyond_legacy_limit["entries"]] == list(
            range(501, 509)
        )
