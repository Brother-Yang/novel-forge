import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import models, schemas
from app.agents import pipeline
from app.api.chapters import update_chapter_content
from app.db_base import Base
from app.llm.base import ModelResponseError
from app.tasks import book_generation


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database = create_engine(f"sqlite:///{tmp_path / 'draft-lifecycle.db'}")
    with database.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    Base.metadata.create_all(database)
    try:
        yield database
    finally:
        database.dispose()


def _seed(
    engine: Engine, *, outline: str = "", status: str = "empty"
) -> tuple[str, str]:
    with Session(engine) as db:
        chapter = models.Chapter(
            book=models.Book(title="合成书稿"),
            title="待生成章",
            outline=outline,
            status=status,
        )
        db.add(chapter)
        db.flush()
        book_id, chapter_id = chapter.book_id, chapter.id
        db.commit()
        return book_id, chapter_id


class DraftModel:
    def __init__(self, *, empty: bool = False) -> None:
        super().__init__()
        self.empty = empty
        self.waiting = asyncio.Event()
        self.resume = asyncio.Event()
        self.closed = asyncio.Event()

    async def chat_stream(self, prompt: str, *, system: str) -> AsyncIterator[str]:
        try:
            if self.empty:
                yield " \n\t"
                return
            yield "未完成的模型输出"
            self.waiting.set()
            await self.resume.wait()
            raise ModelResponseError("合成断流")
        finally:
            self.closed.set()


def _use_model(monkeypatch: pytest.MonkeyPatch, model: DraftModel) -> None:
    monkeypatch.setattr(pipeline, "get_llm", lambda: model)
    monkeypatch.setattr(
        pipeline.memory, "build_chapter_context", AsyncMock(return_value="")
    )


async def _collect(db: Session, chapter_id: str) -> list[pipeline.Event]:
    return [event async for event in pipeline.gen_chapter_body(db, chapter_id)]


@pytest.mark.parametrize(
    "outline, idle", [("", "empty"), (" \t", "empty"), ("有效章纲", "outline")]
)
@pytest.mark.parametrize("outcome", ["empty", "error", "cancel"])
async def test_incomplete_generation_restores_status_and_leaves_no_memory_work(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    outline: str,
    idle: str,
    outcome: str,
) -> None:
    _, chapter_id = _seed(engine, outline=outline, status=idle)
    model = DraftModel(empty=outcome == "empty")
    _use_model(monkeypatch, model)
    with Session(engine) as generator:
        if outcome == "empty":
            events = await _collect(generator, chapter_id)
            assert events[-1] == ("error", "模型未生成有效章节正文,请重试")
        else:
            running = asyncio.create_task(_collect(generator, chapter_id))
            try:
                await asyncio.wait_for(model.waiting.wait(), timeout=2)
                with Session(engine) as observer:
                    chapter = observer.get(models.Chapter, chapter_id)
                    assert chapter is not None and chapter.status == "drafting"
                if outcome == "cancel":
                    running.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await running
                else:
                    model.resume.set()
                    with pytest.raises(ModelResponseError, match="合成断流"):
                        await running
            finally:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)

    assert model.closed.is_set()
    with Session(engine) as observer:
        chapter = observer.get(models.Chapter, chapter_id)
        assert chapter is not None and chapter.content is None
        assert (chapter.status, chapter.outline, chapter.word_count) == (
            idle,
            outline,
            0,
        )
        assert observer.scalars(select(models.MemorySyncJob.id)).all() == []


@pytest.mark.parametrize("change", ["body", "metadata"])
async def test_closing_an_old_stream_preserves_changes_from_another_session(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _, chapter_id = _seed(engine, outline="旧章纲", status="outline")
    model = DraftModel()
    _use_model(monkeypatch, model)
    with Session(engine) as generator:
        async with aclosing(pipeline.gen_chapter_body(generator, chapter_id)) as stream:
            assert await anext(stream) == ("token", "未完成的模型输出")
            with Session(engine) as author:
                if change == "body":
                    saved = update_chapter_content(
                        chapter_id,
                        schemas.ChapterContentUpdate(body="作者新保存的正文"),
                        author,
                    )
                    assert saved.status == "done"
                else:
                    chapter = author.get(models.Chapter, chapter_id)
                    assert chapter is not None and chapter.status == "drafting"
                    chapter.title, chapter.outline = "作者新章名", "作者新章纲"
                    author.commit()

    assert model.closed.is_set()
    with Session(engine) as observer:
        chapter = observer.get(models.Chapter, chapter_id)
        assert chapter is not None
        if change == "body":
            assert chapter.status == "done"
            assert (
                chapter.content is not None
                and chapter.content.body == "作者新保存的正文"
            )
            assert observer.scalars(select(models.MemorySyncJob.id)).all()
        else:
            assert (chapter.title, chapter.outline, chapter.status) == (
                "作者新章名",
                "作者新章纲",
                "drafting",
            )
            assert observer.scalars(select(models.MemorySyncJob.id)).all() == []


def test_restart_only_repairs_empty_drafts_while_owning_sqlite_write_lock(
    engine: Engine,
) -> None:
    book_id, empty_id = _seed(engine, status="drafting")
    with Session(engine) as db:
        outlined = models.Chapter(
            book_id=book_id,
            title="有纲草稿",
            outline="保留章纲",
            status="drafting",
            content=models.ChapterContent(body=" \n"),
            word_count=2,
        )
        authored = models.Chapter(
            book_id=book_id,
            title="已有作者草稿",
            status="drafting",
            content=models.ChapterContent(body="保留作者草稿"),
            word_count=6,
        )
        db.add_all([outlined, authored])
        db.flush()
        outlined_id, authored_id = outlined.id, authored.id
        db.commit()

    saved_body = schemas.ChapterContentUpdate(body="恢复后作者保存的正文")
    attempted = False

    def concurrent_writer(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal attempted
        if attempted or not statement.startswith("SELECT chapters.id"):
            return
        attempted = True
        with Session(engine) as author:
            author.connection().exec_driver_sql("PRAGMA busy_timeout=0")
            with pytest.raises(OperationalError, match="database is locked"):
                update_chapter_content(empty_id, saved_body, author)

    event.listen(engine, "after_cursor_execute", concurrent_writer)
    try:
        with Session(engine) as recovery:
            book_generation.reset_empty_drafts(recovery, book_id)
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_writer)
    assert attempted

    with Session(engine) as author:
        empty = author.get(models.Chapter, empty_id)
        outlined = author.get(models.Chapter, outlined_id)
        authored = author.get(models.Chapter, authored_id)
        assert empty is not None and empty.status == "empty"
        assert outlined is not None and (outlined.status, outlined.word_count) == (
            "outline",
            0,
        )
        assert authored is not None and authored.content is not None
        assert (authored.status, authored.content.body) == ("drafting", "保留作者草稿")
        saved = update_chapter_content(empty_id, saved_body, author)
        assert saved.status == "done" and saved.body == saved_body.body


async def test_batch_skips_later_chapter_saved_while_first_chapter_is_generating(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id, first_id = _seed(engine, outline="第一章纲", status="outline")
    with Session(engine) as db:
        second = models.Chapter(
            book_id=book_id,
            title="第二章",
            outline="第二章纲",
            order_index=1,
            status="outline",
        )
        task = models.GenerationTask(book_id=book_id, type="book", status="running")
        db.add_all([second, task])
        db.flush()
        second_id, task_id = second.id, task.id
        db.commit()

    streaming_first = asyncio.Event()
    finish_first = asyncio.Event()
    prompts: list[str] = []

    class PausedModel:
        async def chat_stream(self, prompt: str, *, system: str) -> AsyncIterator[str]:
            prompts.append(prompt)
            if len(prompts) == 1:
                streaming_first.set()
                await finish_first.wait()
            yield "模型生成的正文"

    monkeypatch.setattr(pipeline, "get_llm", PausedModel)
    monkeypatch.setattr(
        pipeline.memory, "build_chapter_context", AsyncMock(return_value="")
    )
    memory = AsyncMock(return_value=True)
    monkeypatch.setattr(pipeline.memory_sync, "process_chapter_memory", memory)

    def chapter_jobs(db: Session) -> set[tuple[str, str, str]]:
        return {
            (job.id, job.revision, job.source_hash)
            for job in db.scalars(
                select(models.MemorySyncJob).where(
                    models.MemorySyncJob.source_id == second_id
                )
            )
        }

    with Session(engine) as runner:
        task = runner.get(models.GenerationTask, task_id)
        assert task is not None
        running = asyncio.create_task(book_generation.run_chapter_bodies(runner, task))
        try:
            await asyncio.wait_for(streaming_first.wait(), timeout=2)
            with Session(engine) as author:
                saved = update_chapter_content(
                    second_id,
                    schemas.ChapterContentUpdate(body="作者先完成的第二章"),
                    author,
                )
                assert saved.status == "done"
                author_jobs = chapter_jobs(author)
                assert author_jobs
            finish_first.set()
            await asyncio.wait_for(running, timeout=2)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    assert len(prompts) == 1 and memory.await_count == 1
    with Session(engine) as db:
        first = db.get(models.Chapter, first_id)
        second = db.get(models.Chapter, second_id)
        task = db.get(models.GenerationTask, task_id)
        assert first is not None and first.content is not None
        assert first.status == "done" and first.content.body == "模型生成的正文"
        assert second is not None and second.content is not None
        assert second.status == "done" and second.content.body == "作者先完成的第二章"
        assert chapter_jobs(db) == author_jobs
        assert task is not None and (task.status, task.progress) == ("done", 100)
        assert book_generation.load_checkpoint(task)["done_count"] == 2
