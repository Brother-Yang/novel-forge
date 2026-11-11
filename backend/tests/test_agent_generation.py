"""智能体任务运行时(app/tasks/agent_generation.py)测试:进度计算、
checkpoint 决策历史截断、启动前置校验(繁忙/后端不支持)、以及
run_agent_task 对 done/error/暂停三种终态的处理是否正确落库。

用真实的临时 SQLite 数据库验证落库效果,用假的 orchestrator.run_agent
生成器驱动 run_agent_task,不依赖真实 LLM 与真实生成管线。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.agents import orchestrator
from app.db_base import Base
from app.tasks import admission, agent_generation
from app.tasks import book_generation as runtime


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/agent_generation_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _book(db: Session, **kwargs: object) -> models.Book:
    book = models.Book(
        title="测试之书", genre="玄幻", tone="热血", target_words=10000, **kwargs
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def _task(db: Session, book: models.Book, **kwargs: object) -> models.GenerationTask:
    task = models.GenerationTask(
        book_id=book.id,
        type="agent",
        status="running",
        progress=0,
        checkpoint_json="{}",
        **kwargs,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


# ---------- 启动前置校验 ----------
def test_start_agent_generation_rejects_unsupported_backend(db, monkeypatch):
    book = _book(db)
    monkeypatch.setattr(admission, "supports_tool_calling", lambda: False)

    with pytest.raises(admission.UnsupportedBackend):
        admission.start_existing_book(db, book, "agent")


def test_start_agent_generation_rejects_when_book_busy(db, monkeypatch):
    book = _book(db)
    db.add(models.GenerationTask(book_id=book.id, type="book", status="running"))
    db.commit()
    monkeypatch.setattr(admission, "supports_tool_calling", lambda: True)

    with pytest.raises(runtime.BookBusyError):
        admission.start_existing_book(db, book, "agent")


# ---------- checkpoint 决策历史截断 ----------
def test_append_decision_truncates_history(db):
    book = _book(db)
    task = _task(db, book)

    for i in range(agent_generation.MAX_DECISION_LOG + 10):
        agent_generation._append_decision(task, f"entry-{i}")

    ckpt = runtime.load_checkpoint(task)
    decisions = ckpt["decisions"]
    assert isinstance(decisions, list)
    assert len(decisions) == agent_generation.MAX_DECISION_LOG
    assert decisions[-1] == f"entry-{agent_generation.MAX_DECISION_LOG + 9}"
    assert decisions[0] == "entry-10"  # 最早的 10 条已被挤出


# ---------- 进度计算 ----------
def test_update_progress_noop_without_chapters(db):
    book = _book(db)
    task = _task(db, book)

    agent_generation._update_progress(db, task)

    assert task.progress == 0


def test_update_progress_computes_ratio_with_cap(db):
    book = _book(db)
    for i in range(10):
        db.add(
            models.Chapter(
                book_id=book.id,
                order_index=i,
                title=f"第{i}章",
                status="done" if i < 9 else "outline",
            )
        )
    db.commit()
    for chapter in db.query(models.Chapter).filter_by(book_id=book.id, status="done"):
        chapter.content = models.ChapterContent(body="已完成正文")
    db.commit()
    task = _task(db, book)

    agent_generation._update_progress(db, task)

    # done=9, total=10 -> 5 + int(0.9*90) = 86
    assert task.progress == 86


def test_update_progress_never_exceeds_cap(db):
    book = _book(db)
    for i in range(5):
        db.add(
            models.Chapter(
                book_id=book.id, order_index=i, title=f"第{i}章", status="done"
            )
        )
    db.commit()
    for chapter in db.query(models.Chapter).filter_by(book_id=book.id, status="done"):
        chapter.content = models.ChapterContent(body="已完成正文")
    db.commit()
    task = _task(db, book)

    agent_generation._update_progress(db, task)

    assert task.progress == 95  # 全部完成也要留给 finish_book 收尾,不到 100


# ---------- run_agent_task:三种终态落库是否正确 ----------
async def test_run_agent_task_marks_done_on_finish(db, monkeypatch):
    book = _book(db)
    chapter = models.Chapter(book_id=book.id, title="终章", status="done")
    chapter.content = models.ChapterContent(body="结局正文")
    db.add(chapter)
    db.commit()
    task = _task(db, book)

    async def fake_run_agent(db_, book_id):
        yield ("decision", "write_worldbuilding({})")
        yield ("done", "")

    monkeypatch.setattr(orchestrator, "run_agent", fake_run_agent)

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert task.status == "done"
    assert task.progress == 100
    ckpt = runtime.load_checkpoint(task)
    decisions = ckpt["decisions"]
    assert isinstance(decisions, list)
    assert any("write_worldbuilding" in entry for entry in decisions)


async def test_run_agent_task_marks_error_on_error_event(db, monkeypatch):
    book = _book(db)
    task = _task(db, book)

    async def fake_run_agent(db_, book_id):
        yield ("error", "决策调用失败:网络超时")

    monkeypatch.setattr(orchestrator, "run_agent", fake_run_agent)

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert task.status == "error"
    assert "网络超时" in task.error


async def test_failed_progress_update_does_not_publish_incomplete_agent_event(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _book(db)
    task = _task(db, book)
    task_id = task.id

    async def one_event(_db: Session, _book_id: str) -> AsyncIterator[tuple[str, str]]:
        yield ("log", "未提交的模型事件")

    def fail_progress(_db: Session, _task: models.GenerationTask) -> None:
        raise RuntimeError("injected progress failure")

    monkeypatch.setattr(orchestrator, "run_agent", one_event)
    monkeypatch.setattr(agent_generation, "_update_progress", fail_progress)
    await agent_generation.run_agent_task(db, task)

    with Session(db.get_bind()) as reader:
        persisted = reader.get(models.GenerationTask, task_id)
        assert persisted is not None and persisted.status == "error"
        assert persisted.progress == 0 and persisted.trace_sequence == 1
        assert "decisions" not in runtime.load_checkpoint(persisted)
        rows = reader.scalars(
            select(models.AgentTraceEntry).where(
                models.AgentTraceEntry.task_id == task_id
            )
        ).all()
        assert [(row.sequence, row.kind) for row in rows] == [(1, "error")]


async def test_run_agent_task_paused_mid_flight_keeps_paused_not_error(db, monkeypatch):
    """任务在决策循环中途被外部标记为 paused 时,应干净退出,
    既不覆盖为 error,也不再处理暂停之后的事件。"""

    async def fake_run_agent(db_, book_id):
        yield ("decision", "write_worldbuilding({})")
        task.status = "paused"
        db.commit()
        yield ("decision", "create_characters({})")  # 不应被处理到

    book = _book(db)
    task = _task(db, book)
    monkeypatch.setattr(orchestrator, "run_agent", fake_run_agent)

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert task.status == "paused"
    ckpt = runtime.load_checkpoint(task)
    decisions = ckpt.get("decisions", [])
    assert isinstance(decisions, list)
    assert len(decisions) == 1
