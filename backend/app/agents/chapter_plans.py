"""Stage chapter outline and ordering changes in the caller's transaction."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from . import stages
from .completion import chapter_complete_condition


@dataclass(frozen=True)
class SavedChapterOutline:
    chapter_id: str
    title: str
    outline: str
    order_index: int
    status: str
    complete: bool


@dataclass(frozen=True)
class VolumeOutlineInputs:
    title: str
    summary: str
    order_index: int
    is_final_volume: bool
    chapters: tuple[SavedChapterOutline, ...]


def is_final_volume(db: Session, book_id: str, order_index: int) -> bool:
    max_order = db.scalar(
        select(func.max(models.Volume.order_index)).where(
            models.Volume.book_id == book_id
        )
    )
    return max_order is not None and order_index >= max_order


def volume_outline_inputs(
    db: Session, book_id: str, volume_id: str
) -> VolumeOutlineInputs:
    """Read the author inputs and completion state without loading chapter prose."""
    volume = db.execute(
        select(
            models.Volume.title, models.Volume.summary, models.Volume.order_index
        ).where(models.Volume.id == volume_id, models.Volume.book_id == book_id)
    ).one_or_none()
    if volume is None:
        raise stages.InvalidStage("生成期间分卷已删除或发生变动，请刷新目录")
    title, summary, order_index = volume
    chapters = db.execute(
        select(
            models.Chapter.id,
            models.Chapter.title,
            models.Chapter.outline,
            models.Chapter.order_index,
            models.Chapter.status,
            chapter_complete_condition(),
        )
        .outerjoin(models.ChapterContent)
        .where(models.Chapter.book_id == book_id, models.Chapter.volume_id == volume_id)
        .order_by(models.Chapter.order_index, models.Chapter.id)
    )
    return VolumeOutlineInputs(
        title=title,
        summary=summary,
        order_index=order_index,
        is_final_volume=is_final_volume(db, book_id, order_index),
        chapters=tuple(SavedChapterOutline(*chapter) for chapter in chapters.tuples()),
    )


def ordered_volume_chapters(
    db: Session, book_id: str, volume_id: str
) -> list[models.Chapter]:
    return list(
        db.scalars(
            select(models.Chapter)
            .where(
                models.Chapter.book_id == book_id,
                models.Chapter.volume_id == volume_id,
            )
            .order_by(models.Chapter.order_index, models.Chapter.id)
        )
    )


def _insert_position(
    db: Session,
    volume: models.Volume,
    count: int,
    retained_ids: set[str] | None,
) -> tuple[int, list[models.Chapter], int]:
    if retained_ids is None:
        last_order = db.scalar(
            select(func.max(models.Chapter.order_index)).where(
                models.Chapter.book_id == volume.book_id
            )
        )
        return (last_order if last_order is not None else -1) + 1, [], 0

    rows = db.execute(
        select(models.Chapter, models.Volume.order_index)
        .outerjoin(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(models.Chapter.book_id == volume.book_id)
        .order_by(models.Chapter.order_index, models.Chapter.id)
        .execution_options(populate_existing=True)
    ).all()
    existing = [chapter for chapter, _ in rows]
    current = [chapter for chapter in existing if chapter.volume_id == volume.id]
    if {chapter.id for chapter in current} != retained_ids:
        raise stages.InvalidStage("生成期间卷内章节发生变动，请核对目录")
    if len({chapter.order_index for chapter in existing}) != len(existing):
        raise stages.InvalidStage("已有章节序号重复，请先调整目录顺序")
    following = next(
        (
            chapter.order_index
            for chapter, volume_order in rows
            if volume_order is not None and volume_order > volume.order_index
        ),
        None,
    )
    if following is None:
        return (existing[-1].order_index + 1 if existing else 0), [], 0
    if any(chapter.order_index >= following for chapter in current):
        raise stages.InvalidStage("本卷与后续卷章节交错，请先调整目录顺序")

    preceding = [
        chapter.order_index for chapter in existing if chapter.order_index < following
    ]
    start = preceding[-1] + 1 if preceding else min(0, following)
    offset = max(0, start + count - following)
    # Insert only the missing space and preserve every existing relative order.
    shifted = (
        [chapter for chapter in existing if chapter.order_index >= following]
        if offset
        else []
    )
    return start, shifted, offset


def stage_new_outlines(
    db: Session,
    volume: models.Volume,
    sections: Sequence[stages.Section],
    *,
    retained_ids: set[str] | None,
) -> None:
    """Append manual outlines or fill a recovered volume before later volumes."""
    start, shifted, offset = _insert_position(db, volume, len(sections), retained_ids)
    for chapter in shifted:
        chapter.order_index += offset
    db.add_all(
        models.Chapter(
            book_id=volume.book_id,
            volume_id=volume.id,
            title=section.title,
            order_index=start + index,
            outline=section.body,
            status="outline",
        )
        for index, section in enumerate(sections)
    )


@dataclass(frozen=True)
class ReplanTarget:
    chapter_id: str
    order_index: int
    title: str
    outline: str
    status: str
    body: str | None


def replan_targets(chapters: Sequence[models.Chapter]) -> list[ReplanTarget]:
    targets: list[ReplanTarget] = []
    for chapter in chapters:
        if chapter.status == "done":
            continue
        body = chapter.content.body if chapter.content else None
        if body and body.strip():
            continue
        targets.append(
            ReplanTarget(
                chapter.id,
                chapter.order_index,
                chapter.title,
                chapter.outline,
                chapter.status,
                body,
            )
        )
    return targets


def stage_replanned_outlines(
    db: Session,
    book_id: str,
    volume_id: str,
    targets: list[ReplanTarget],
    sections: Sequence[stages.Section],
) -> None:
    """Verify the whole batch under the SQLite write lock before staging changes."""
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    volume = db.get(models.Volume, volume_id)
    if volume is None or volume.book_id != book_id:
        raise stages.InvalidStage("生成期间分卷发生变动，请刷新目录后重试")
    chapters = ordered_volume_chapters(db, book_id, volume_id)
    if replan_targets(chapters) != targets:
        raise stages.InvalidStage("生成期间待规划章节发生变动，已保留当前内容")
    pending_ids = {target.chapter_id for target in targets}
    retained = [
        stages.Section(chapter.title, chapter.outline)
        for chapter in chapters
        if chapter.id not in pending_ids
    ]
    stages.require_distinct(sections, retained, "章节")
    pending = [chapter for chapter in chapters if chapter.id in pending_ids]
    for chapter, section in zip(pending, sections, strict=True):
        chapter.title = section.title
        chapter.outline = section.body
        chapter.status = "outline"
