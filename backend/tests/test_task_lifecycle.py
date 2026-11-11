import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.db_base import Base
from app.tasks import admission
from app.tasks import book_generation as runtime


@pytest.fixture
async def sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'task-lifecycle.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(runtime, "SessionLocal", factory)
    monkeypatch.setattr(runtime, "_running", {})
    monkeypatch.setattr(runtime, "_active_books", {})
    try:
        yield factory
    finally:
        await runtime.stop_all_generation()
        engine.dispose()


def _add_running_task(
    db: Session, book_id: str, *, task_id: str = "book-task", task_type: str = "book"
) -> models.GenerationTask:
    task = models.GenerationTask(
        id=task_id,
        book_id=book_id,
        type=task_type,
        status="running",
        progress=25,
        checkpoint_json='{"stage":"chapter_bodies","done_count":2}',
    )
    db.add(task)
    db.flush()
    return task


def _fail_session_reads(
    monkeypatch: pytest.MonkeyPatch, sessions: sessionmaker[Session], fail_on: set[int]
) -> None:
    opened = 0

    def session_with_fault() -> Session:
        nonlocal opened
        opened += 1
        session = sessions()
        if opened in fail_on:
            error = OperationalError(
                "private SQL detail", (), RuntimeError("private connection detail")
            )
            monkeypatch.setattr(session, "get", Mock(side_effect=error))
        return session

    monkeypatch.setattr(runtime, "SessionLocal", session_with_fault)


@pytest.mark.parametrize(
    "fail_on", [{2}, {2, 3}], ids=["worker-read", "worker-and-error-write"]
)
async def test_failed_worker_lookup_can_retry_without_losing_checkpoint(
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fail_on: set[int],
) -> None:
    with sessions() as db:
        book = models.Book(title="已有书稿")
        db.add(book)
        db.flush()
        task = _add_running_task(db, book.id)
        task_id, book_id = task.id, book.id
        db.commit()

    ran: list[str] = []

    async def complete(db: Session, task: models.GenerationTask) -> None:
        ran.append(task.id)
        assert (task.progress, runtime.load_checkpoint(task)["done_count"]) == (25, 2)
        task.status = "done"
        task.progress = 100
        db.commit()

    monkeypatch.setitem(runtime._task_runners, "book", complete)
    _fail_session_reads(monkeypatch, sessions, fail_on)

    with caplog.at_level(logging.ERROR):
        assert runtime.launch(task_id)
        await runtime._running[task_id]
    assert ran == [] and not runtime._running and not runtime._active_books
    assert task_id in caplog.text and "private SQL detail" not in caplog.text
    assert "private connection detail" not in caplog.text

    with sessions() as db:
        task = db.get(models.GenerationTask, task_id)
        assert task is not None
        expected_status = "running" if 3 in fail_on else "error"
        assert task.status == expected_status
        assert "private" not in task.error
        if expected_status == "error":
            runtime.ensure_book_idle(db, book_id)
        runtime.resume_task(db, task_id)
    await runtime._running[task_id]
    with sessions() as db:
        task = db.get(models.GenerationTask, task_id)
        assert task is not None and (task.status, task.progress, task.error) == (
            "done",
            100,
            "",
        )
    assert ran == [task_id]


async def test_admission_returns_committed_task_error_if_launch_lookup_fails(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_session_reads(monkeypatch, sessions, {1})
    with sessions() as db:
        task = admission.start_new_book(
            db, models.Book(title="测试", brief="创作想法"), "novel"
        )
        task_id, book_id = task.id, task.book_id
        assert task.status == "error" and task.error

    with sessions() as db:
        assert db.get(models.Book, book_id) is not None
        assert db.scalars(select(models.GenerationTask.id)).all() == [task_id]
        runtime.ensure_book_idle(db, book_id)


async def test_schedule_failure_closes_coroutine_and_releases_capacity(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with sessions() as db:
        book = models.Book(title="可重新调度")
        db.add(book)
        db.flush()
        task_id = _add_running_task(db, book.id).id
        db.commit()

    with monkeypatch.context() as failure:
        failure.setattr(
            asyncio.get_running_loop(),
            "create_task",
            Mock(side_effect=RuntimeError("调度失败")),
        )
        assert not runtime.launch(task_id)

    assert not runtime._active_books and not runtime._running
    with sessions() as db:
        task = db.get(models.GenerationTask, task_id)
        assert task is not None and task.status == "error"


async def test_legacy_chapter_task_does_not_run_whole_book_or_reserve_its_lease(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with sessions() as db:
        book = models.Book(title="旧任务所在书")
        db.add(book)
        db.flush()
        legacy = _add_running_task(db, book.id, task_id="a-legacy", task_type="chapter")
        legacy.target_id = "old-chapter-id"
        _add_running_task(db, book.id, task_id="b-book")
        db.commit()

    ran: list[str] = []

    async def complete(db: Session, task: models.GenerationTask) -> None:
        ran.append(task.id)
        task.status = "done"
        db.commit()

    monkeypatch.setitem(runtime._task_runners, "book", complete)
    assert runtime.recover_running_tasks() == 1
    await runtime._running["b-book"]
    assert ran == ["b-book"]
    with sessions() as db:
        legacy = db.get(models.GenerationTask, "a-legacy")
        assert legacy is not None and legacy.status == "error"
        assert legacy.target_id == "old-chapter-id"
        assert "重新发起生成" in legacy.error
