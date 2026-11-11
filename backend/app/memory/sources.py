"""Read-only source identity and revision checks shared by retrieval and sync."""

import hashlib
import json
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import models
from . import facts

WORLD = "worldbuilding"
CHAPTER = "chapter_summary"
FACTS = facts.SOURCE_TYPE
VOLUME = "volume_summary"


def _hash(values: object) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def worldbuilding_hash(item: models.Worldbuilding) -> str:
    return _hash([item.title, item.md_content])


def chapter_body_hash(chapter: models.Chapter) -> str:
    body = chapter.content.body if chapter.content else ""
    return _hash(body) if body.strip() else ""


def chapter_fact_body_hash(chapter: models.Chapter) -> str:
    return facts.fingerprint(chapter.content.body) if chapter.content else ""


def chapter_hash(chapter: models.Chapter) -> str:
    body_hash = chapter_body_hash(chapter)
    return _hash([chapter.title, body_hash]) if body_hash else ""


def valid_summary(chapter: models.Chapter) -> str:
    summary = chapter.summary
    if (
        summary
        and summary.source_hash
        and summary.source_hash == chapter_body_hash(chapter)
    ):
        return summary.summary
    return ""


def source_hash(db: Session, book_id: str, source_type: str, source_id: str) -> str:
    if source_type == WORLD:
        item = db.get(models.Worldbuilding, source_id)
        return worldbuilding_hash(item) if item and item.book_id == book_id else ""
    if source_type in (CHAPTER, FACTS):
        chapter = db.get(models.Chapter, source_id)
        if not chapter or chapter.book_id != book_id:
            return ""
        if source_type == CHAPTER:
            return chapter_hash(chapter)
        body_hash = chapter_fact_body_hash(chapter)
        return (
            facts.extraction_fingerprint(
                body_hash, facts.roster_for_book(db, book_id).fingerprint()
            )
            if body_hash
            else ""
        )
    if source_type != VOLUME:
        return ""
    volume = db.get(models.Volume, source_id)
    if not volume or volume.book_id != book_id:
        return ""
    return volume_hash(volume, completed_volume_chapters(db, book_id, source_id))


def completed_volume_chapters(
    db: Session, book_id: str, volume_id: str
) -> Sequence[models.Chapter]:
    """Chapter order used for both the volume fingerprint and the summary input."""
    return db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id == volume_id,
            models.Chapter.book_id == book_id,
            models.Chapter.status == "done",
        )
        .options(
            selectinload(models.Chapter.content), selectinload(models.Chapter.summary)
        )
        .order_by(models.Chapter.order_index, models.Chapter.id)
    ).all()


def volume_hash(volume: models.Volume, chapters: Iterable[models.Chapter]) -> str:
    return _hash(
        [
            volume.title,
            [
                [ch.id, fingerprint, valid_summary(ch)]
                for ch in chapters
                if (fingerprint := chapter_hash(ch))
            ],
        ]
    )


def find_job(
    db: Session, book_id: str, source_type: str, source_id: str
) -> models.MemorySyncJob | None:
    return db.scalar(
        select(models.MemorySyncJob)
        .where(
            models.MemorySyncJob.book_id == book_id,
            models.MemorySyncJob.source_type == source_type,
            models.MemorySyncJob.source_id == source_id,
        )
        .execution_options(populate_existing=True)
    )
