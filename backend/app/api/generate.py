from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from .. import models, schemas
from ..agents import observability, pipeline
from ..database import get_db
from ..tasks import agent_generation, book_generation, novel_generation
from ..tasks.agent_generation import UnsupportedBackend
from ..tasks.book_generation import BookBusyError

router = APIRouter()


async def _sse(gen: AsyncIterator[tuple[str, str]]):
    try:
        async for event, data in gen:
            yield {"event": event, "data": data}
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": str(exc)}


# ---------- 分层生成(SSE 流式) ----------
@router.post("/generate/worldbuilding")
async def generate_worldbuilding(
    payload: schemas.GenerateWorldbuildingReq, db: Session = Depends(get_db)
):
    return EventSourceResponse(_sse(pipeline.gen_worldbuilding(db, payload.book_id)))


@router.post("/generate/characters")
async def generate_characters(
    payload: schemas.GenerateCharactersReq, db: Session = Depends(get_db)
):
    return EventSourceResponse(
        _sse(pipeline.gen_characters(db, payload.book_id, payload.count))
    )


@router.post("/generate/outline")
async def generate_outline(
    payload: schemas.GenerateOutlineReq, db: Session = Depends(get_db)
):
    return EventSourceResponse(
        _sse(pipeline.gen_outline(db, payload.book_id, payload.volume_count))
    )


@router.post("/generate/chapter-outlines")
async def generate_chapter_outlines(
    payload: schemas.GenerateChapterOutlinesReq, db: Session = Depends(get_db)
):
    return EventSourceResponse(
        _sse(
            pipeline.gen_chapter_outlines(
                db, payload.volume_id, payload.chapter_count
            )
        )
    )


@router.post("/generate/chapter")
async def generate_chapter(
    payload: schemas.GenerateChapterReq, db: Session = Depends(get_db)
):
    return EventSourceResponse(
        _sse(pipeline.gen_chapter_body(db, payload.chapter_id, payload.words))
    )


# ---------- 全书批量任务 ----------
@router.post("/generate/book", response_model=schemas.GenerationTaskOut)
async def generate_book(payload: schemas.GenerateBookReq, db: Session = Depends(get_db)):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    try:
        return book_generation.start_book_generation(db, payload.book_id)
    except BookBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/generate/novel", response_model=schemas.NovelGenerationOut)
async def generate_novel(payload: schemas.GenerateNovelReq, db: Session = Depends(get_db)):
    brief = payload.brief.strip()
    if not brief:
        raise HTTPException(400, "请填写一段故事想法")
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
        raise HTTPException(409, str(exc)) from exc
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
    if not book.brief:
        book.brief = brief
        db.commit()
    try:
        return novel_generation.start_novel_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/generate/agent", response_model=schemas.NovelGenerationOut)
async def generate_agent(payload: schemas.GenerateAgentReq, db: Session = Depends(get_db)):
    """一句话/简介 -> 智能体自主决策创作全流程(type=agent),与固定流水线并存。"""
    brief = payload.brief.strip()
    if not brief:
        raise HTTPException(400, "请填写一段故事想法")
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
        raise HTTPException(409, str(exc)) from exc
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
    if not book.brief:
        book.brief = brief
        db.commit()
    try:
        return agent_generation.start_agent_generation(db, book.id)
    except BookBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except UnsupportedBackend as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/books/{book_id}/tasks", response_model=list[schemas.GenerationTaskOut])
def list_tasks(book_id: str, db: Session = Depends(get_db)):
    return db.scalars(
        select(models.GenerationTask)
        .where(models.GenerationTask.book_id == book_id)
        .order_by(models.GenerationTask.created_at.desc())
    ).all()


@router.get("/tasks/{task_id}", response_model=schemas.GenerationTaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(models.GenerationTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/tasks/{task_id}/trace", response_model=list[schemas.AgentTraceEntryOut])
def get_task_trace(task_id: int, db: Session = Depends(get_db)):
    """智能体任务(type=agent)的完整执行轨迹,供调试/前端展开查看。"""
    task = db.get(models.GenerationTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return observability.list_trace(db, task_id)


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: int, db: Session = Depends(get_db)):
    book_generation.pause_task(db, task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: int, db: Session = Depends(get_db)):
    book_generation.resume_task(db, task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    book_generation.resume_task(db, task_id)
    return {"ok": True}
