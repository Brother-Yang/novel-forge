from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter()


@router.get("/books/{book_id}/chapters", response_model=list[schemas.ChapterOut])
def list_chapters(
    book_id: int,
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
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


@router.post("/chapters", response_model=schemas.ChapterOut)
def create_chapter(payload: schemas.ChapterCreate, db: Session = Depends(get_db)):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    chapter = models.Chapter(**payload.model_dump())
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter


@router.patch("/chapters/{chapter_id}", response_model=schemas.ChapterOut)
def update_chapter(
    chapter_id: int, payload: schemas.ChapterUpdate, db: Session = Depends(get_db)
):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(chapter, k, v)
    db.commit()
    db.refresh(chapter)
    return chapter


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    db.delete(chapter)
    db.commit()
    return {"ok": True}


@router.post("/chapters/reorder")
def reorder_chapters(payload: schemas.ReorderRequest, db: Session = Depends(get_db)):
    """拖拽排序:批量更新 order_index 与所属卷。"""
    for item in payload.items:
        chapter = db.get(models.Chapter, item.id)
        if chapter:
            chapter.order_index = item.order_index
            if item.volume_id is not None:
                chapter.volume_id = item.volume_id
    db.commit()
    return {"ok": True}


@router.get("/chapters/{chapter_id}/content", response_model=schemas.ChapterContentOut)
def get_chapter_content(chapter_id: int, db: Session = Depends(get_db)):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    body = chapter.content.body if chapter.content else ""
    return schemas.ChapterContentOut(chapter_id=chapter_id, body=body)


@router.put("/chapters/{chapter_id}/content", response_model=schemas.ChapterContentOut)
def update_chapter_content(
    chapter_id: int,
    payload: schemas.ChapterContentUpdate,
    db: Session = Depends(get_db),
):
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    if chapter.content:
        chapter.content.body = payload.body
    else:
        db.add(models.ChapterContent(chapter_id=chapter_id, body=payload.body))
    chapter.word_count = len(payload.body)
    if payload.body.strip():
        chapter.status = "done"
    db.commit()
    return schemas.ChapterContentOut(chapter_id=chapter_id, body=payload.body)
