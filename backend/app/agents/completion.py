"""以落库正文验证章节/全书完成情况,供工具和任务恢复共用。"""

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from .. import models
from .errors import GenerationError

# Python str.strip() 识别的 Unicode 空白;SQLite trim 默认只去掉 ASCII 空格。
_BODY_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def chapter_complete_condition() -> ColumnElement[bool]:
    """SQL predicate matching chapter_is_complete, including an absent body."""
    return and_(
        models.Chapter.status == "done",
        func.coalesce(func.trim(models.ChapterContent.body, _BODY_WHITESPACE), "")
        != "",
    )


def chapter_counts(db: Session, book_id: str) -> tuple[int, int]:
    """只返回总数与实际完成数,避免每条轨迹将全书正文读入内存。"""
    total, done = db.execute(
        select(
            func.count(models.Chapter.id),
            func.count(case((chapter_complete_condition(), 1))),
        )
        .outerjoin(models.ChapterContent)
        .where(models.Chapter.book_id == book_id)
    ).one()
    return total, done


def chapter_is_complete(chapter: models.Chapter) -> bool:
    return (
        chapter.status == "done"
        and chapter.content is not None
        and bool(chapter.content.body.strip())
    )


def chapter_completion_queue(db: Session, book_id: str) -> tuple[int, list[str]]:
    """Return the total and the pending chapter IDs in narrative order."""
    total = 0
    pending: list[str] = []
    for chapter_id, complete in db.execute(
        select(models.Chapter.id, chapter_complete_condition())
        .outerjoin(models.ChapterContent)
        .where(models.Chapter.book_id == book_id)
        .order_by(models.Chapter.order_index, models.Chapter.id)
    ):
        total += 1
        if not complete:
            pending.append(chapter_id)
    return total, pending


def require_book_complete(db: Session, book_id: str) -> None:
    """不能仅凭模型结束信号或 done 元数据宣布完成。"""
    if db.scalar(select(models.Book.id).where(models.Book.id == book_id)) is None:
        raise GenerationError("书籍不存在")
    total, done = chapter_counts(db, book_id)
    if not total:
        raise GenerationError("全书尚无已完成的非空章节正文,请先生成章节")
    if pending := total - done:
        raise GenerationError(f"仍有 {pending} 章未完成或正文为空,请继续生成后重试")
    populated_volumes = select(models.Chapter.volume_id).where(
        models.Chapter.book_id == book_id, models.Chapter.volume_id.is_not(None)
    )
    empty_volume = db.scalar(
        select(models.Volume.id)
        .where(
            models.Volume.book_id == book_id,
            models.Volume.id.not_in(populated_volumes),
        )
        .limit(1)
    )
    if empty_volume is not None:
        raise GenerationError("仍有卷未生成章节,请继续生成后重试")
