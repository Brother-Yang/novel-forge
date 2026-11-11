import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..memory import facts, sync, vector_store
from ..tasks import book_generation

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/books", response_model=list[schemas.BookOut])
def list_books(db: Session = Depends(get_db)):
    return db.scalars(select(models.Book).order_by(models.Book.created_at.desc())).all()


@router.post("/books", response_model=schemas.BookOut)
def create_book(payload: schemas.BookCreate, db: Session = Depends(get_db)):
    book = models.Book(**payload.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


@router.get("/books/{book_id}", response_model=schemas.BookOut)
def get_book(book_id: str, db: Session = Depends(get_db)):
    book = db.get(models.Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    return book


@router.patch("/books/{book_id}", response_model=schemas.BookOut)
def update_book(
    book_id: str, payload: schemas.BookUpdate, db: Session = Depends(get_db)
):
    book = db.get(models.Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(book, k, v)
    db.commit()
    db.refresh(book)
    return book


@router.delete("/books/{book_id}")
async def delete_book(book_id: str, db: Session = Depends(get_db)):
    if db.get(models.Book, book_id) is None:
        raise HTTPException(404, "书籍不存在")
    try:
        with book_generation.book_deletion(book_id):
            await book_generation.cancel_book_tasks(db, book_id)
            try:
                await sync.suspend_book_memory(book_id)
                try:
                    # Preserve the manuscript for a delete retry if vector cleanup fails.
                    vector_store.delete_book(book_id)
                except Exception as exc:
                    logger.error(
                        "vector cleanup failed book=%s type=%s",
                        book_id,
                        type(exc).__name__,
                    )
                    raise HTTPException(
                        503, "书籍记忆清理失败,生成任务已暂停,请重试删除"
                    ) from exc
                book = db.get(models.Book, book_id)
                if book is not None:
                    db.delete(book)
                    db.commit()
            finally:
                # Success cascades all jobs; failure can resume the retained book.
                sync.resume_book_memory(book_id)
    except book_generation.BookBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.get("/books/{book_id}/tree", response_model=schemas.BookTree)
def get_book_tree(book_id: str, db: Session = Depends(get_db)):
    """卷 + 章目录树,不含正文;按上千章设计,只返回轻量字段。"""
    if not db.get(models.Book, book_id):
        raise HTTPException(404, "书籍不存在")

    volumes = db.scalars(
        select(models.Volume)
        .where(models.Volume.book_id == book_id)
        .order_by(models.Volume.order_index)
    ).all()
    chapters = db.scalars(
        select(models.Chapter)
        .where(models.Chapter.book_id == book_id)
        .order_by(models.Chapter.order_index)
    ).all()

    by_volume: dict[str, list[schemas.TreeChapter]] = {v.id: [] for v in volumes}
    loose: list[schemas.TreeChapter] = []
    for c in chapters:
        item = schemas.TreeChapter(
            id=c.id,
            title=c.title,
            order_index=c.order_index,
            status=c.status,
            word_count=c.word_count,
        )
        if c.volume_id and c.volume_id in by_volume:
            by_volume[c.volume_id].append(item)
        else:
            loose.append(item)

    return schemas.BookTree(
        volumes=[
            schemas.TreeVolume(
                id=v.id,
                title=v.title,
                order_index=v.order_index,
                chapters=by_volume[v.id],
            )
            for v in volumes
        ],
        loose_chapters=loose,
    )


@router.get("/books/{book_id}/plot_threads", response_model=list[schemas.PlotThreadOut])
def list_plot_threads(book_id: str, db: Session = Depends(get_db)):
    threads = db.scalars(
        select(models.PlotThread).where(models.PlotThread.book_id == book_id)
    ).all()
    derived = facts.replay(db, book_id).threads
    return [
        schemas.PlotThreadOut.model_validate(thread).model_copy(
            update={"derived_status": derived.get(thread.id)},
        )
        for thread in threads
    ]
