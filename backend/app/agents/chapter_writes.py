"""Save generated chapter text only if the author has not changed the chapter."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..memory import sync as memory_sync
from .errors import GenerationError

DELETED_DURING_GENERATION = "生成期间章节已删除，请刷新目录"


@dataclass(frozen=True)
class DraftSnapshot:
    chapter_id: str
    book_id: str
    volume_id: str | None
    order_index: int
    title: str
    outline: str
    status: str
    word_count: int
    revision_approved: bool
    body: str | None


def _snapshot(chapter: models.Chapter) -> DraftSnapshot:
    return DraftSnapshot(
        chapter_id=chapter.id,
        book_id=chapter.book_id,
        volume_id=chapter.volume_id,
        order_index=chapter.order_index,
        title=chapter.title,
        outline=chapter.outline,
        status=chapter.status,
        word_count=chapter.word_count,
        revision_approved=chapter.revision_approved,
        body=chapter.content.body if chapter.content else None,
    )


def _lock_chapter(db: Session, chapter_id: str) -> models.Chapter | None:
    # Discard the previous read snapshot. The write lock protects the fresh
    # chapter and its separate content row until the caller commits or rolls back.
    db.rollback()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    return db.get(models.Chapter, chapter_id)


def begin_draft(
    db: Session, chapter_id: str
) -> tuple[models.Chapter, DraftSnapshot, str]:
    """Capture the inputs and mark the chapter drafting before calling the model."""
    try:
        chapter = _lock_chapter(db, chapter_id)
        if chapter is None:
            raise GenerationError("章节不存在")
        book_title = db.scalar(
            select(models.Book.title).where(models.Book.id == chapter.book_id)
        )
        if book_title is None:
            raise GenerationError("书籍不存在")
        chapter.status = "drafting"
        expected = _snapshot(chapter)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return chapter, expected, book_title


def save_result(db: Session, expected: DraftSnapshot, body: str) -> models.Chapter:
    """Check current data and commit the result and any memory work together."""
    try:
        chapter = _lock_chapter(db, expected.chapter_id)
        if chapter is None:
            raise GenerationError(DELETED_DURING_GENERATION)
        if _snapshot(chapter) != expected:
            raise GenerationError("生成期间章节已修改，已保留当前内容，请刷新后重试")
        if body:
            if chapter.content:
                chapter.content.body = body
            else:
                chapter.content = models.ChapterContent(body=body)
            chapter.word_count = len(body)
            chapter.status = "done"
            memory_sync.enqueue_chapter(db, chapter)
        else:
            # Empty output preserves old text and leaves the chapter retryable.
            chapter.status = "outline"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return chapter
