from itertools import batched

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from .. import models, schemas
from ..agents.chapter_versions import (
    ChapterContentConflict,
    content_version,
    require_content_version,
)
from ..database import get_db
from ..memory import sync as memory_sync

router = APIRouter()
_REORDER_BATCH_SIZE = 500


def _validate_volume_owner(volume_owner: str | None, book_id: str) -> None:
    if volume_owner is None:
        raise HTTPException(404, "卷不存在")
    if volume_owner != book_id:
        raise HTTPException(400, "卷与章节必须属于同一本书")


def _validate_volume(db: Session, volume_id: str | None, book_id: str) -> None:
    if volume_id is not None:
        owner_id = db.scalar(
            select(models.Volume.book_id).where(models.Volume.id == volume_id)
        )
        _validate_volume_owner(owner_id, book_id)


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
        .order_by(models.Chapter.order_index, models.Chapter.id)
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
    if payload.status == "done":
        db.rollback()
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    if "volume_id" in payload.model_fields_set:
        _validate_volume(db, payload.volume_id, chapter.book_id)
    if payload.status == "done" and (
        chapter.content is None or not chapter.content.body.strip()
    ):
        db.rollback()
        raise HTTPException(400, "正文为空，不能将章节标为完成")
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
    if not chapter_ids:
        return {"ok": True}

    db.rollback()
    with db.begin():
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chapters = {
            chapter.id: chapter
            for batch in batched(chapter_ids, _REORDER_BATCH_SIZE)
            for chapter in db.scalars(
                select(models.Chapter)
                .where(models.Chapter.id.in_(batch))
                .options(
                    load_only(
                        models.Chapter.id,
                        models.Chapter.book_id,
                        models.Chapter.volume_id,
                        models.Chapter.order_index,
                        models.Chapter.status,
                    )
                )
            )
        }
        target_volumes = {
            item.volume_id for item in payload.items if item.volume_id is not None
        }
        volume_books = {
            volume_id: owner_id
            for batch in batched(target_volumes, _REORDER_BATCH_SIZE)
            for volume_id, owner_id in db.execute(
                select(models.Volume.id, models.Volume.book_id).where(
                    models.Volume.id.in_(batch)
                )
            )
        }

        book_id: str | None = None
        # Validate the complete batch before mutating any chapter.
        for item in payload.items:
            chapter = chapters.get(item.id)
            if chapter is None:
                raise HTTPException(404, "章节不存在")
            if book_id is not None and chapter.book_id != book_id:
                raise HTTPException(400, "排序章节必须属于同一本书")
            book_id = chapter.book_id
            if item.volume_id is not None:
                _validate_volume_owner(
                    volume_books.get(item.volume_id), chapter.book_id
                )

        affected_volumes: set[str] = set()
        for item in payload.items:
            chapter = chapters[item.id]
            if (chapter.order_index, chapter.volume_id) == (
                item.order_index,
                item.volume_id,
            ):
                continue
            if chapter.status == "done":
                affected_volumes.update(
                    volume_id
                    for volume_id in (chapter.volume_id, item.volume_id)
                    if volume_id is not None
                )
            chapter.order_index = item.order_index
            chapter.volume_id = item.volume_id
        if book_id is not None:
            for volume_id in affected_volumes:
                memory_sync.enqueue(db, book_id, memory_sync.VOLUME, volume_id)
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
