from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agents.chapter_versions import (
    ChapterContentConflict,
    content_version,
    require_content_version,
)
from ..database import get_db
from ..memory import sync as memory_sync

router = APIRouter()


def _validate_volume(db: Session, volume_id: str | None, book_id: str) -> None:
    if volume_id is None:
        return
    volume = db.get(models.Volume, volume_id)
    if not volume:
        raise HTTPException(404, "卷不存在")
    if volume.book_id != book_id:
        raise HTTPException(400, "卷与章节必须属于同一本书")


@router.get("/books/{book_id}/chapters", response_model=list[schemas.ChapterOut])
def list_chapters(
    book_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """分页返回章节元数据(不含正文),支持上千章。"""
    return db.scalars(
        select(models.Chapter)
        .where(models.Chapter.book_id == book_id)
        .order_by(models.Chapter.order_index)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/chapters/{chapter_id}", response_model=schemas.ChapterOut)
def get_chapter(chapter_id: str, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


@router.post("/chapters", response_model=schemas.ChapterOut)
def create_chapter(payload: schemas.ChapterCreate, db: Session = Depends(get_db)):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    _validate_volume(db, payload.volume_id, payload.book_id)
    chapter = models.Chapter(**payload.model_dump())
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter


@router.patch("/chapters/{chapter_id}", response_model=schemas.ChapterOut)
def update_chapter(
    chapter_id: str, payload: schemas.ChapterUpdate, db: Session = Depends(get_db)
):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    if "volume_id" in payload.model_fields_set:
        _validate_volume(db, payload.volume_id, chapter.book_id)
    previous = (chapter.title, chapter.volume_id, chapter.order_index, chapter.status)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(chapter, k, v)
    old_title, old_volume_id, _, old_status = previous
    current = (chapter.title, chapter.volume_id, chapter.order_index, chapter.status)
    if chapter.title != old_title and memory_sync.chapter_body_hash(chapter):
        memory_sync.enqueue_chapter(db, chapter, old_volume_id=old_volume_id)
    elif previous != current and "done" in (old_status, chapter.status):
        for volume_id in {old_volume_id, chapter.volume_id}:
            if volume_id:
                memory_sync.enqueue(db, chapter.book_id, memory_sync.VOLUME, volume_id)
    db.commit()
    db.refresh(chapter)
    return chapter


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: str, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    book_id, volume_id = chapter.book_id, chapter.volume_id
    memory_sync.enqueue(db, book_id, memory_sync.CHAPTER, chapter.id, removed=True)
    memory_sync.clear_chapter_facts(db, chapter)
    db.delete(chapter)
    if volume_id:
        memory_sync.enqueue(db, book_id, memory_sync.VOLUME, volume_id)
    db.commit()
    return {"ok": True}


@router.post("/chapters/reorder")
def reorder_chapters(payload: schemas.ReorderRequest, db: Session = Depends(get_db)):
    """拖拽排序:批量更新 order_index 与所属卷。

    volume_id 按传入值设置(None 表示移到未归卷区),前端每次拖拽都会带上目标卷。
    """
    chapter_ids = [item.id for item in payload.items]
    if len(chapter_ids) != len(set(chapter_ids)):
        raise HTTPException(400, "排序列表不能包含重复章节")
    chapters: list[models.Chapter] = []
    book_id: str | None = None
    # 先完整校验,避免后续查询触发 autoflush,使无效批次部分落库。
    for item in payload.items:
        chapter = db.get(models.Chapter, item.id)
        if not chapter:
            raise HTTPException(404, "章节不存在")
        if book_id is not None and chapter.book_id != book_id:
            raise HTTPException(400, "排序章节必须属于同一本书")
        book_id = chapter.book_id
        _validate_volume(db, item.volume_id, chapter.book_id)
        chapters.append(chapter)
    affected_volumes: set[str] = set()
    for item, chapter in zip(payload.items, chapters, strict=True):
        if (chapter.order_index, chapter.volume_id) == (
            item.order_index,
            item.volume_id,
        ):
            continue
        if chapter.status == "done":
            affected_volumes.update(
                volume_id
                for volume_id in (chapter.volume_id, item.volume_id)
                if volume_id
            )
        chapter.order_index = item.order_index
        chapter.volume_id = item.volume_id
    if book_id is not None:
        for volume_id in affected_volumes:
            memory_sync.enqueue(db, book_id, memory_sync.VOLUME, volume_id)
    db.commit()
    return {"ok": True}


@router.get("/chapters/{chapter_id}/content", response_model=schemas.ChapterContentOut)
def get_chapter_content(chapter_id: str, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    body = chapter.content.body if chapter.content else ""
    return schemas.ChapterContentOut(
        chapter_id=chapter_id, body=body, version=content_version(body)
    )


@router.put(
    "/chapters/{chapter_id}/content", response_model=schemas.ChapterContentSaved
)
def update_chapter_content(
    chapter_id: str,
    payload: schemas.ChapterContentUpdate,
    db: Session = Depends(get_db),
):
    db.rollback()
    try:
        # Hold the SQLite write lock from reading the current version through
        # saving the text and memory jobs, so concurrent saves cannot both pass.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chapter = db.get(models.Chapter, chapter_id)
        if not chapter:
            raise HTTPException(404, "章节不存在")
        current_body = chapter.content.body if chapter.content else ""
        require_content_version(current_body, payload.expected_version)
        if current_body != payload.body:
            chapter.revision_approved = False
        if chapter.content:
            chapter.content.body = payload.body
        else:
            chapter.content = models.ChapterContent(body=payload.body)
        if payload.body.strip():
            chapter.word_count = len(payload.body)
            chapter.status = "done"
        else:
            chapter.word_count = 0
            chapter.status = "outline" if chapter.outline.strip() else "empty"
        memory_sync.enqueue_chapter(db, chapter)
        saved = schemas.ChapterContentSaved(
            chapter_id=chapter_id,
            body=payload.body,
            version=content_version(payload.body),
            status=chapter.status,
            word_count=chapter.word_count,
        )
        db.commit()
    except ChapterContentConflict as exc:
        db.rollback()
        raise HTTPException(409, "章节正文已在其他窗口更新，请重新加载后保存") from exc
    except Exception:
        db.rollback()
        raise
    return saved
