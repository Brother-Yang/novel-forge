"""Lock and verify author-owned structure before persisting a generated batch."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .stages import InvalidStage, Section

type StructureRow = tuple[str, str, *tuple[object, ...]]


@dataclass(frozen=True)
class StructureSnapshot:
    rows: tuple[StructureRow, ...]

    @property
    def sections(self) -> list[Section]:
        return [Section(title, body) for title, body, *_ in self.rows]


def worldbuilding(db: Session, book_id: str) -> StructureSnapshot:
    item = models.Worldbuilding
    rows = db.execute(
        select(item.title, item.md_content, item.id, item.category)
        .where(item.book_id == book_id)
        .order_by(item.id)
    )
    return StructureSnapshot(tuple(rows.tuples()))


def characters(db: Session, book_id: str) -> StructureSnapshot:
    item = models.Character
    rows = db.execute(
        select(item.name, item.md_content, item.id, item.order_index, item.state_json)
        .where(item.book_id == book_id)
        .order_by(item.order_index, item.id)
    )
    return StructureSnapshot(tuple(rows.tuples()))


def outline(db: Session, book_id: str) -> tuple[StructureSnapshot, StructureSnapshot]:
    volume, thread = models.Volume, models.PlotThread
    volumes = db.execute(
        select(volume.title, volume.summary, volume.id, volume.order_index)
        .where(volume.book_id == book_id)
        .order_by(volume.order_index, volume.id)
    )
    threads = db.execute(
        select(thread.name, thread.description, thread.id, thread.status)
        .where(thread.book_id == book_id)
        .order_by(thread.id)
    )
    return StructureSnapshot(tuple(volumes.tuples())), StructureSnapshot(
        tuple(threads.tuples())
    )


def lock_current[T](
    db: Session,
    book_id: str,
    read: Callable[[Session, str], T],
    expected: T | None,
    label: str,
) -> T:
    """Leave the write transaction open for the caller's atomic stage commit."""
    db.rollback()
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if db.scalar(select(models.Book.id).where(models.Book.id == book_id)) is None:
            raise InvalidStage("生成期间书籍已删除，请刷新目录")
        current = read(db, book_id)
        if expected is not None and current != expected:
            raise InvalidStage(f"生成期间{label}已变动，已保留当前内容")
    except Exception:
        db.rollback()
        raise
    return current
