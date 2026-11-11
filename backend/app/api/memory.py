from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..memory import sync

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


def _require_book(db: Session, book_id: str) -> None:
    if not db.get(models.Book, book_id):
        raise HTTPException(404, "书籍不存在")


@router.get("/books/{book_id}/memory/status")
def memory_status(book_id: str, db: DbSession) -> sync.MemoryStatus:
    _require_book(db, book_id)
    return sync.memory_status(db, book_id)


@router.get("/books/{book_id}/memory/progress")
def memory_progress(book_id: str, db: DbSession) -> sync.MemoryProgress:
    _require_book(db, book_id)
    return sync.memory_progress(db, book_id)


@router.post("/books/{book_id}/memory/repair")
def repair_memory(book_id: str, db: DbSession) -> sync.MemoryProgress:
    _require_book(db, book_id)
    return sync.repair_book(db, book_id)
