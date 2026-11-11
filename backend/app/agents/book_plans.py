"""Apply model plans only while the book's planning inputs still match."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .. import models
from . import planner
from .errors import GenerationError


@dataclass(frozen=True)
class BookPlanSnapshot:
    book_id: str
    title: str
    brief: str
    intro: str
    genre: str
    tone: str
    target_words: int
    key_elements: str


def _snapshot(book: models.Book) -> BookPlanSnapshot:
    return BookPlanSnapshot(
        book_id=book.id,
        title=book.title,
        brief=book.brief,
        intro=book.intro,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
        key_elements=book.key_elements,
    )


async def prepare(
    db: Session, book: models.Book
) -> tuple[BookPlanSnapshot, planner.NovelPlan]:
    expected = _snapshot(book)
    db.rollback()
    plan = await planner.plan_novel(
        expected.brief or expected.intro,
        title=(
            ""
            if planner.is_placeholder_title(expected.title, expected.brief)
            else expected.title
        ),
        genre=expected.genre,
        tone=expected.tone,
        target_words=expected.target_words,
    )
    return expected, plan


def stage_if_current(
    db: Session, expected: BookPlanSnapshot, plan: planner.NovelPlan
) -> models.Book:
    """Check fresh fields under the write lock and leave commit to the caller."""
    db.rollback()
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        book = db.get(models.Book, expected.book_id)
        if book is None:
            raise GenerationError("书籍不存在")
        if _snapshot(book) != expected:
            raise GenerationError("规划期间书籍信息已修改，已保留修改，请重试规划")
        planner.apply_plan_to_book(book, plan)
    except Exception:
        db.rollback()
        raise
    return book
