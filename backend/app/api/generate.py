import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette.types import Receive, Scope, Send

from .. import models, schemas
from ..agents import observability, pipeline
from ..agents.errors import log_failure, public_failure, redact_secrets
from ..database import get_db
from ..tasks import agent_generation, book_generation, novel_generation
from ..tasks.agent_generation import UnsupportedBackend
from ..tasks.book_generation import BookBusyError

router = APIRouter()
logger = logging.getLogger(__name__)


class _GenerationResponse(EventSourceResponse):
    """Release reservations even if disconnect precedes stream iteration."""

    def __init__(
        self,
        db: Session,
        gen: AsyncIterator[tuple[str, str]],
        lease: book_generation.GenerationLease,
        chapter_id: str | None,
    ) -> None:
        self.lease = lease
        # Run model work separately from the socket writer. A delete can cancel
        # and drain this producer even if the browser is slow to read SSE data.
        producer, consumer = anyio.create_memory_object_stream[dict[str, str]](1)
        self._consumer = consumer

        async def run() -> None:
            lease.handle = asyncio.current_task()
            with producer:
                try:
                    if lease.cancelled:
                        await producer.send({"event": "error", "data": "生成已停止"})
                        return
                    async for event, data in gen:
                        if event in ("log", "error"):
                            data = redact_secrets(data)
                        if event == "error":
                            logger.warning(
                                "SSE generation stopped book=%s chapter=%s operation=%s: %s",
                                lease.book_id,
                                chapter_id,
                                lease.owner,
                                data,
                            )
                        await producer.send({"event": event, "data": data})
                        if event in ("done", "error"):
                            break
                except asyncio.CancelledError:
                    if not lease.cancelled:
                        raise
                    # Keep the socket writer alive long enough to send the
                    # terminal event after explicitly cancelling its producer.
                    with suppress(anyio.WouldBlock, anyio.BrokenResourceError):
                        producer.send_nowait({"event": "error", "data": "生成已停止"})
                except Exception as exc:  # noqa: BLE001
                    log_failure(
                        logger,
                        exc,
                        operation=f"SSE:{lease.owner}",
                        book_id=lease.book_id,
                        chapter_id=chapter_id,
                    )
                    await producer.send({"event": "error", "data": public_failure(exc)})
                finally:
                    try:
                        if isinstance(gen, AsyncGenerator):
                            with anyio.CancelScope(shield=True):
                                await gen.aclose()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "SSE cleanup failed book=%s chapter=%s operation=%s type=%s",
                            lease.book_id,
                            chapter_id,
                            lease.owner,
                            type(exc).__name__,
                        )
                    finally:
                        try:
                            # Drain any open transaction before letting another
                            # request generate or delete this book.
                            db.close()
                        finally:
                            book_generation.release_generation(lease)

        super().__init__(consumer, data_sender_callable=run, send_timeout=30)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            with self._consumer:
                await super().__call__(scope, receive, send)
        finally:
            book_generation.release_generation(self.lease)


def _manual_stream(
    db: Session,
    book_id: str,
    operation: str,
    gen: AsyncIterator[tuple[str, str]],
    *,
    chapter_id: str | None = None,
) -> EventSourceResponse:
    if db.get(models.Book, book_id) is None:
        raise HTTPException(404, "书籍不存在")
    try:
        lease = book_generation.claim_manual_generation(db, book_id, operation)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return _GenerationResponse(db, gen, lease, chapter_id)


# ---------- 分层生成(SSE 流式) ----------
@router.post("/generate/worldbuilding")
async def generate_worldbuilding(
    payload: schemas.GenerateWorldbuildingReq, db: Session = Depends(get_db)
):
    return _manual_stream(
        db,
        payload.book_id,
        "worldbuilding",
        pipeline.gen_worldbuilding(db, payload.book_id),
    )


@router.post("/generate/characters")
async def generate_characters(
    payload: schemas.GenerateCharactersReq, db: Session = Depends(get_db)
):
    return _manual_stream(
        db,
        payload.book_id,
        "characters",
        pipeline.gen_characters(db, payload.book_id, payload.count),
    )


@router.post("/generate/outline")
async def generate_outline(
    payload: schemas.GenerateOutlineReq, db: Session = Depends(get_db)
):
    return _manual_stream(
        db,
        payload.book_id,
        "outline",
        pipeline.gen_outline(db, payload.book_id, payload.volume_count),
    )


@router.post("/generate/chapter-outlines")
async def generate_chapter_outlines(
    payload: schemas.GenerateChapterOutlinesReq, db: Session = Depends(get_db)
):
    volume = db.get(models.Volume, payload.volume_id)
    if volume is None:
        raise HTTPException(404, "分卷不存在")
    return _manual_stream(
        db,
        volume.book_id,
        "chapter-outlines",
        pipeline.gen_chapter_outlines(db, payload.volume_id, payload.chapter_count),
    )


@router.post("/generate/chapter")
async def generate_chapter(
    payload: schemas.GenerateChapterReq, db: Session = Depends(get_db)
):
    chapter = db.get(models.Chapter, payload.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    return _manual_stream(
        db,
        chapter.book_id,
        "chapter",
        pipeline.gen_chapter_body(db, payload.chapter_id, payload.words),
        chapter_id=chapter.id,
    )


# ---------- 全书批量任务 ----------
@router.post("/generate/book", response_model=schemas.GenerationTaskOut)
async def generate_book(
    payload: schemas.GenerateBookReq, db: Session = Depends(get_db)
):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    try:
        return book_generation.start_book_generation(db, payload.book_id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/generate/novel", response_model=schemas.NovelGenerationOut)
async def generate_novel(
    payload: schemas.GenerateNovelReq, db: Session = Depends(get_db)
):
    brief = payload.brief.strip()
    if not brief:
        raise HTTPException(400, "请填写一段故事想法")
    _require_available_slot()
    title = (payload.title or "").strip() or "未命名"
    book = models.Book(
        title=title,
        intro="",
        brief=brief,
        genre=payload.genre.strip(),
        tone=payload.tone.strip(),
        target_words=payload.target_words,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    try:
        task = novel_generation.start_novel_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return schemas.NovelGenerationOut(
        book=schemas.BookOut.model_validate(book),
        task=schemas.GenerationTaskOut.model_validate(task),
    )


@router.post("/generate/novel/from-book", response_model=schemas.GenerationTaskOut)
async def generate_novel_from_book(
    payload: schemas.GenerateNovelFromBookReq, db: Session = Depends(get_db)
):
    book = db.get(models.Book, payload.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    brief = (book.brief or book.intro or "").strip()
    if not brief:
        raise HTTPException(400, "请先填写简介,或从首页使用一句话出书")
    try:
        book_generation.ensure_book_idle(db, book.id)
        if not book.brief:
            book.brief = brief
            db.commit()
        return novel_generation.start_novel_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/generate/agent", response_model=schemas.NovelGenerationOut)
async def generate_agent(
    payload: schemas.GenerateAgentReq, db: Session = Depends(get_db)
):
    """一句话/简介 -> 智能体自主决策创作全流程(type=agent),与固定流水线并存。"""
    brief = payload.brief.strip()
    if not brief:
        raise HTTPException(400, "请填写一段故事想法")
    _require_available_slot()
    title = (payload.title or "").strip() or "未命名"
    book = models.Book(
        title=title,
        intro="",
        brief=brief,
        genre=payload.genre.strip(),
        tone=payload.tone.strip(),
        target_words=payload.target_words,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    try:
        task = agent_generation.start_agent_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except UnsupportedBackend as exc:
        raise HTTPException(400, str(exc)) from exc
    return schemas.NovelGenerationOut(
        book=schemas.BookOut.model_validate(book),
        task=schemas.GenerationTaskOut.model_validate(task),
    )


@router.post("/generate/agent/from-book", response_model=schemas.GenerationTaskOut)
async def generate_agent_from_book(
    payload: schemas.GenerateAgentFromBookReq, db: Session = Depends(get_db)
):
    book = db.get(models.Book, payload.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    brief = (book.brief or book.intro or "").strip()
    if not brief:
        raise HTTPException(400, "请先填写简介,或从首页使用一句话出书")
    try:
        book_generation.ensure_book_idle(db, book.id)
        if not book.brief:
            book.brief = brief
            db.commit()
        return agent_generation.start_agent_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except UnsupportedBackend as exc:
        raise HTTPException(400, str(exc)) from exc


def _require_available_slot() -> None:
    try:
        book_generation.ensure_generation_capacity()
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/books/{book_id}/tasks", response_model=list[schemas.GenerationTaskOut])
def list_tasks(book_id: str, db: Session = Depends(get_db)):
    return db.scalars(
        select(models.GenerationTask)
        .where(models.GenerationTask.book_id == book_id)
        .order_by(models.GenerationTask.created_at.desc())
    ).all()


@router.get("/tasks/{task_id}", response_model=schemas.GenerationTaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(models.GenerationTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/tasks/{task_id}/trace", response_model=list[schemas.AgentTraceEntryOut])
def get_task_trace(task_id: str, db: Session = Depends(get_db)):
    """兼容旧客户端，按执行顺序返回最多 500 条轨迹。"""
    task = db.get(models.GenerationTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return observability.list_trace(db, task_id)


@router.get("/tasks/{task_id}/trace/page", response_model=schemas.AgentTracePageOut)
def get_task_trace_page(
    task_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, ge=1, le=2**53 - 1),
    after: int | None = Query(default=None, ge=0, le=2**53 - 1),
) -> schemas.AgentTracePageOut:
    """默认返回末页；before 查询较早的记录，after 查询之后的记录。"""
    if before is not None and after is not None:
        raise HTTPException(422, "before 和 after 只能指定一个")
    if db.get(models.GenerationTask, task_id) is None:
        raise HTTPException(404, "任务不存在")
    entries, has_more = observability.list_trace_page(
        db, task_id, limit, before=before, after=after
    )
    return schemas.AgentTracePageOut(
        entries=[
            schemas.AgentTracePageEntryOut.model_validate(entry) for entry in entries
        ],
        has_more=has_more,
    )


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, db: Session = Depends(get_db)):
    book_generation.pause_task(db, task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
@router.post("/tasks/{task_id}/retry")
async def resume_task(task_id: str, db: Session = Depends(get_db)):
    try:
        book_generation.resume_task(db, task_id)
    except BookBusyError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": True}
