"""Book deletion drains active tasks and removes every SQL/vector dependent."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import chromadb
import pytest
from chromadb.errors import NotFoundError
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import database, models
from app.api.books import delete_book
from app.db_base import Base
from app.memory import vector_store
from app.tasks import admission
from app.tasks import book_generation as runtime


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'deletion.db'}")
    event.listen(engine, "connect", database._enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(runtime, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(vector_store, "delete_book", lambda book_id: None)
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(db: Session, title: str) -> str:
    book = models.Book(title=title)
    chapter = models.Chapter(
        book=book,
        title="章节",
        volume=models.Volume(book=book, title="卷"),
        content=models.ChapterContent(body="正文"),
        summary=models.ChapterSummary(summary="摘要"),
    )
    character = models.Character(book=book, name="人物")
    db.add_all([book, chapter, character])
    db.flush()
    task = models.GenerationTask(book_id=book.id, type="book", status="running")
    db.add(task)
    db.flush()
    db.add_all(
        [
            models.Worldbuilding(book_id=book.id, title="设定"),
            models.PlotThread(book_id=book.id, name="线程"),
            models.AgentTraceEntry(
                task_id=task.id, sequence=1, kind="log", payload="轨迹"
            ),
            models.MemorySyncJob(
                book_id=book.id, source_type="chapter_summary", source_id=chapter.id
            ),
            models.ChapterFacts(
                chapter_id=chapter.id,
                source_hash="fixture-facts",
                payload='{"characters":[],"threads":[]}',
            ),
            models.EmbeddingRef(
                book_id=book.id,
                source_type="chapter_summary",
                source_id=chapter.id,
                chroma_id="memory",
            ),
        ]
    )
    db.commit()
    return book.id


@pytest.mark.asyncio
async def test_delete_book_removes_all_dependents_and_preserves_other_book(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted = seed(db, "待删除")
    other = seed(db, "保留")
    cleaned: list[str] = []
    monkeypatch.setattr(vector_store, "delete_book", cleaned.append)
    assert await delete_book(deleted, db) == {"ok": True}
    db.expunge_all()
    assert cleaned == [deleted]
    assert db.get(models.Book, deleted) is None
    assert db.get(models.Book, other) is not None
    for table in Base.metadata.tables.values():
        assert db.execute(select(table)).all(), (
            f"Other book's {table.name} rows must remain"
        )
        if "book_id" in table.c:
            assert (
                db.execute(select(table).where(table.c.book_id == deleted)).all() == []
            )
        assert db.scalar(select(text("count(*)")).select_from(table)) == 1
    assert db.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.asyncio
async def test_delete_cancels_waiting_generator_before_vector_cleanup(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = seed(db, "生成中")
    task = db.scalars(
        select(models.GenerationTask).where(models.GenerationTask.book_id == book_id)
    ).one()
    task_id = task.id
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    writes: list[str] = []

    async def waiting_runner(worker_db: Session, task: models.GenerationTask) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()  # Fake an in-flight model request.
            worker_db.add(models.Worldbuilding(book_id=task.book_id, title="迟到写入"))
            worker_db.commit()
            writes.append("late write")
        finally:
            cancelled.set()

    def vector_cleanup(deleted: str) -> None:
        assert deleted == book_id
        assert cancelled.is_set()
        assert task_id not in runtime._running

    monkeypatch.setitem(runtime._task_runners, "book", waiting_runner)
    monkeypatch.setattr(vector_store, "delete_book", vector_cleanup)
    runtime.launch(task_id)
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert await delete_book(book_id, db) == {"ok": True}
    await asyncio.sleep(0)
    assert writes == []
    assert task_id not in runtime._running
    assert db.get(models.GenerationTask, task_id) is None
    assert db.scalars(select(models.Worldbuilding)).all() == []


@pytest.mark.asyncio
async def test_delete_cancels_task_that_has_not_started_yet(db: Session) -> None:
    book_id = seed(db, "刚启动")
    task_id = db.scalars(select(models.GenerationTask.id)).one()
    runtime.launch(task_id)
    assert await delete_book(book_id, db) == {"ok": True}
    assert task_id not in runtime._running


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_manuscript_and_pauses_recoverable_tasks(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = seed(db, "可重试")

    def unavailable(_book_id: str) -> None:
        raise RuntimeError("Chroma unavailable")

    monkeypatch.setattr(vector_store, "delete_book", unavailable)
    with pytest.raises(HTTPException) as error:
        await delete_book(book_id, db)
    assert error.value.status_code == 503
    db.expire_all()
    assert db.get(models.Book, book_id) is not None
    assert db.scalars(select(models.GenerationTask.status)).one() == "paused"
    assert db.scalars(select(models.ChapterContent.body)).one() == "正文"
    assert book_id not in runtime._deleting_books
    monkeypatch.setattr(vector_store, "delete_book", lambda _book_id: None)
    assert await delete_book(book_id, db) == {"ok": True}


def test_deletion_blocks_start_resume_and_duplicate_delete(db: Session) -> None:
    book_id = seed(db, "互斥")
    book = db.get(models.Book, book_id)
    assert book is not None
    task = db.scalars(select(models.GenerationTask)).one()
    task.status = "paused"
    db.commit()
    with runtime.book_deletion(book_id):
        with pytest.raises(runtime.BookBusyError):
            admission.start_existing_book(db, book, "book")
        with pytest.raises(runtime.BookBusyError):
            runtime.resume_task(db, task.id)
        with pytest.raises(runtime.BookBusyError), runtime.book_deletion(book_id):
            raise AssertionError("Must reject second deletion")
    assert book_id not in runtime._deleting_books


@pytest.mark.asyncio
async def test_delete_unknown_book_does_not_touch_vectors(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_book_id: str) -> None:
        raise AssertionError("Unknown book must not touch Chroma")

    monkeypatch.setattr(vector_store, "delete_book", forbidden)
    with pytest.raises(HTTPException) as error:
        await delete_book("missing", db)
    assert error.value.status_code == 404


def test_deleted_task_stops_cleanly_at_generation_boundary(db: Session) -> None:
    book_id = seed(db, "任务边界")
    task = db.scalars(select(models.GenerationTask)).one()
    task_id = task.id
    with Session(db.get_bind()) as other:
        other.execute(text("DELETE FROM books WHERE id=:id"), {"id": book_id})
        other.commit()
    with pytest.raises(runtime.Paused):
        runtime.ensure_running(db, task)
    assert db.get(models.GenerationTask, task_id, populate_existing=True) is None


def test_vector_cleanup_removes_only_the_selected_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Direct Chroma API: no default embedding downloads or model calls.
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setattr(vector_store, "_client", lambda: client)
    # The db fixture patches this function, so this standalone test intentionally
    # has no db fixture and exercises the production delete method.
    for book_id in ("deleted-book", "retained-book"):
        vector_store.upsert(
            book_id, "summary", [0.1, 0.2], "记忆", {"source_type": "chapter_summary"}
        )
    deleted_collection = vector_store._collection("deleted-book").name
    vector_store.delete_book("deleted-book")
    vector_store.delete_book("deleted-book")  # Repeated cleanup is safe.
    with pytest.raises(NotFoundError):
        client.get_collection(deleted_collection)
    assert vector_store.existing_ids("retained-book") == {"summary"}
