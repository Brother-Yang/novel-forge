"""Versioned memory outbox. Requests commit content and jobs in one SQL transaction.

Network work happens after that commit. Versioned vector IDs plus SQL validation
make an interrupted or superseded write invisible immediately, even if Chroma is
unavailable during deletion. The application runs one process (see runtime lock).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypedDict

from pydantic import ValidationError
from sqlalchemy import delete, false, func, or_, select, true, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, selectinload

from .. import models
from ..llm.budget import ModelBudgetExceeded
from . import facts, vector_store

logger = logging.getLogger(__name__)
WORLD = "worldbuilding"
CHAPTER = "chapter_summary"
FACTS = facts.SOURCE_TYPE
VOLUME = "volume_summary"
_worker: asyncio.Task[None] | None = None
_active: dict[tuple[str, str], _ActiveJob] = {}
_suspended: set[str] = set()


@dataclass(frozen=True)
class _ActiveJob:
    book_id: str
    task: asyncio.Task[bool]


class MissingChapterSummary(Exception):
    """A volume can only summarize the current chapter summaries."""


class MemoryJobStatus(TypedDict):
    id: str
    source_type: str
    source_id: str
    status: str
    attempts: int
    error: str
    updated_at: str


class MemoryStatus(TypedDict):
    pending: int
    running: int
    failed: int
    missing: int
    ready: int
    total: int
    items: list[MemoryJobStatus]


def _hash(values: object) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def chapter_body_hash(chapter: models.Chapter) -> str:
    body = chapter.content.body if chapter.content else ""
    return _hash(body) if body.strip() else ""


def _fact_hash(chapter: models.Chapter) -> str:
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
        return (
            _hash([item.title, item.md_content])
            if item and item.book_id == book_id
            else ""
        )
    if source_type in (CHAPTER, FACTS):
        chapter = db.get(models.Chapter, source_id)
        if not chapter or chapter.book_id != book_id:
            return ""
        return chapter_hash(chapter) if source_type == CHAPTER else _fact_hash(chapter)
    if source_type != VOLUME:
        return ""
    volume = db.get(models.Volume, source_id)
    if not volume or volume.book_id != book_id:
        return ""
    chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id == source_id,
            models.Chapter.book_id == volume.book_id,
            models.Chapter.status == "done",
        )
        .options(
            selectinload(models.Chapter.content), selectinload(models.Chapter.summary)
        )
        .order_by(models.Chapter.order_index, models.Chapter.id)
    ).all()
    return _volume_hash(volume, chapters)


def _volume_hash(volume: models.Volume, chapters: Iterable[models.Chapter]) -> str:
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


def enqueue(
    db: Session,
    book_id: str,
    source_type: str,
    source_id: str,
    *,
    removed: bool = False,
    retry: bool = False,
    force: bool = False,
) -> models.MemorySyncJob:
    """Do not commit: the caller atomically commits this with its source mutation."""
    db.flush()
    fingerprint = "" if removed else source_hash(db, book_id, source_type, source_id)
    db.execute(
        insert(models.MemorySyncJob)
        .values(
            book_id=book_id,
            source_type=source_type,
            source_id=source_id,
            source_hash=fingerprint,
            revision=models.new_id(),
            status="pending",
        )
        .on_conflict_do_update(
            index_elements=["book_id", "source_type", "source_id"],
            set_={
                "source_hash": fingerprint,
                "revision": models.new_id(),
                "status": "pending",
                "error": "",
                "updated_at": func.now(),
            },
            where=or_(
                models.MemorySyncJob.source_hash != fingerprint,
                models.MemorySyncJob.status == "error" if retry else false(),
                true() if force else false(),
            ),
        )
    )
    job = find_job(db, book_id, source_type, source_id)
    if job is None:
        raise RuntimeError("memory job was not persisted")
    return job


def enqueue_chapter(
    db: Session,
    chapter: models.Chapter,
    *,
    old_volume_id: str | None = None,
) -> models.MemorySyncJob:
    job = enqueue(db, chapter.book_id, CHAPTER, chapter.id)
    if _fact_hash(chapter):
        fact_job = enqueue(db, chapter.book_id, FACTS, chapter.id)
        previous_facts = db.get(models.ChapterFacts, chapter.id)
        if previous_facts and previous_facts.source_hash != fact_job.source_hash:
            db.delete(previous_facts)
    else:
        clear_chapter_facts(db, chapter)
    # Remove SQL summaries immediately too; previous-summary fallback must not
    # reuse old facts while an embedding or summary service is unavailable.
    if chapter.summary:
        body_hash = chapter_body_hash(chapter)
        if not body_hash or chapter.summary.source_hash != body_hash:
            chapter.summary = None
    for volume_id in {chapter.volume_id, old_volume_id} - {None}:
        if volume_id:
            enqueue(db, chapter.book_id, VOLUME, volume_id)
    return job


def clear_chapter_facts(db: Session, chapter: models.Chapter) -> None:
    """Facts are SQL-only; remove the job and payload in the caller's transaction."""
    db.execute(
        delete(models.ChapterFacts).where(models.ChapterFacts.chapter_id == chapter.id)
    )
    db.execute(
        delete(models.MemorySyncJob).where(
            models.MemorySyncJob.book_id == chapter.book_id,
            models.MemorySyncJob.source_type == FACTS,
            models.MemorySyncJob.source_id == chapter.id,
        )
    )


def _current(
    db: Session,
    job_id: str,
    revision: str,
    *,
    lock: bool = False,
) -> models.MemorySyncJob | None:
    if lock:
        # Acquire the SQL write lock and validate the revision before changing
        # its derived rows. A CRUD request may update the source on a thread.
        changed = db.scalar(
            update(models.MemorySyncJob)
            .where(
                models.MemorySyncJob.id == job_id,
                models.MemorySyncJob.revision == revision,
                models.MemorySyncJob.status == "running",
            )
            .values(updated_at=func.now())
            .returning(models.MemorySyncJob.id)
        )
        if changed is None:
            db.rollback()
            return None
    db.expire_all()
    job = db.get(models.MemorySyncJob, job_id)
    if not job or job.revision != revision or job.status != "running":
        return None
    if not db.get(models.Book, job.book_id):
        return None
    if source_hash(db, job.book_id, job.source_type, job.source_id) != job.source_hash:
        enqueue(db, job.book_id, job.source_type, job.source_id)
        db.commit()
        return None
    return job


def _safe_error(exc: Exception, operation: str) -> str:
    # Provider messages can contain URLs, request bodies or credentials. Persist
    # and log only the exception class, never str(exc) or an exception traceback.
    if isinstance(exc, MissingChapterSummary):
        return "等待章节摘要修复"
    if isinstance(exc, ModelBudgetExceeded):
        return str(exc)
    if isinstance(exc, facts.InvalidChapterFacts):
        return "模型返回的事实格式或证据不符合本章，未更新人物或剧情线程，可重试"
    labels = {
        "chapter_summary": "生成章摘要",
        "chapter_facts": "提取本章事实",
        "volume_summary": "生成卷摘要",
        "embedding": "生成向量",
        "delete_vector": "清理旧向量",
        "upsert_vector": "保存新向量",
    }
    return f"{labels.get(operation, '同步记忆')}失败（{type(exc).__name__}），可重试"


def _replace_ref(db: Session, job: models.MemorySyncJob, doc_id: str) -> None:
    db.execute(
        delete(models.EmbeddingRef).where(
            models.EmbeddingRef.book_id == job.book_id,
            models.EmbeddingRef.source_type == job.source_type,
            models.EmbeddingRef.source_id == job.source_id,
        )
    )
    db.add(
        models.EmbeddingRef(
            book_id=job.book_id,
            source_type=job.source_type,
            source_id=job.source_id,
            chroma_id=doc_id,
        )
    )


async def _write_chapter_facts(
    db: Session,
    job: models.MemorySyncJob,
    revision: str,
) -> bool:
    from .context import get_llm

    job_id, book_id, chapter_id, source_hash = (
        job.id,
        job.book_id,
        job.source_id,
        job.source_hash,
    )
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter or not chapter.content:
        return False
    body = chapter.content.body
    roster = facts.roster_for_book(db, book_id)
    saved = db.get(models.ChapterFacts, chapter_id)
    batch: facts.FactBatch | None = None
    if saved and saved.source_hash == source_hash:
        try:
            batch = facts.FactBatch.model_validate_json(saved.payload)
            facts.validate(batch, body, roster)
        except (ValidationError, facts.InvalidChapterFacts):
            batch = None
    if batch is None:
        db.rollback()
        if roster.characters or roster.threads:
            batch = await facts.extract(get_llm(), body, roster)
        else:
            batch = facts.FactBatch(characters=[], threads=[])
    current = _current(db, job_id, revision, lock=True)
    if current is None:
        return False
    # The author's cast may change while the model is running. Never attach a
    # fact to an entity that no longer belongs to this book.
    facts.validate(batch, body, facts.roster_for_book(db, book_id))
    db.merge(
        models.ChapterFacts(
            chapter_id=chapter_id,
            source_hash=source_hash,
            payload=batch.model_dump_json(exclude_none=True),
        )
    )
    current.status = "done"
    db.commit()
    return True


async def process_job(db: Session, job_id: str) -> bool:
    """Run the current revision once, sharing the outcome across callers.

    Call only after committing source changes. Worker and pipeline can share
    the task without holding an open SQL transaction while waiting for it.
    """
    job = db.get(models.MemorySyncJob, job_id, populate_existing=True)
    if job is None or job.book_id in _suspended:
        return False
    key = (job_id, job.revision)
    active = _active.get(key)
    if active:
        db.rollback()
        return await asyncio.shield(active.task)
    if job.status == "done":
        return True
    if job.status != "pending":
        return False
    book_id, source_type, source_id = job.book_id, job.source_type, job.source_id
    db.rollback()
    task = asyncio.create_task(
        _process_job(db, job_id, key[1], book_id, source_type, source_id)
    )
    _active[key] = _ActiveJob(book_id, task)
    try:
        return await task
    finally:
        _active.pop(key, None)


async def _process_job(
    db: Session,
    job_id: str,
    revision: str,
    book_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Rebuild derived data; a repair never modifies chapter content/status."""
    from ..agents import prompts
    from .context import get_llm

    operation = "claim"
    try:
        changed = db.scalar(
            update(models.MemorySyncJob)
            .where(
                models.MemorySyncJob.id == job_id,
                models.MemorySyncJob.revision == revision,
                models.MemorySyncJob.status == "pending",
            )
            .values(
                status="running",
                attempts=models.MemorySyncJob.attempts + 1,
                error="",
                updated_at=func.now(),
            )
            .returning(models.MemorySyncJob.id)
        )
        db.commit()
        if changed is None:
            return False
        job = _current(db, job_id, revision)
        if job is None:
            return False
        if not job.source_hash:
            job = _current(db, job_id, revision, lock=True)
            if job is None:
                return False
            if source_type in (WORLD, CHAPTER):
                operation = "delete_vector"
                vector_store.delete_source(book_id, source_type, source_id)
            db.execute(
                delete(models.EmbeddingRef).where(
                    models.EmbeddingRef.book_id == book_id,
                    models.EmbeddingRef.source_type == source_type,
                    models.EmbeddingRef.source_id == source_id,
                )
            )
            # Tombstones are required only until physical cleanup succeeds.
            db.delete(job)
            db.commit()
            return True

        if source_type == FACTS:
            operation = "chapter_facts"
            return await _write_chapter_facts(db, job, revision)

        summary = ""
        if source_type == WORLD:
            item = db.get(models.Worldbuilding, source_id)
            if item is None:
                return False
            text = f"{item.title}\n{item.md_content}"
        elif source_type == CHAPTER:
            chapter = db.get(models.Chapter, source_id)
            if chapter is None:
                return False
            summary = valid_summary(chapter)
            body = chapter.content.body if chapter.content else ""
            if not summary:
                operation = "chapter_summary"
                # Include the ending as well as the opening of a long chapter.
                excerpt = (
                    body
                    if len(body) <= 12000
                    else body[:8000] + "\n[…]\n" + body[-4000:]
                )
                db.rollback()
                summary = (
                    await get_llm().chat(
                        prompts.SUMMARY_USER.format(body=excerpt),
                        system=prompts.SUMMARY_SYSTEM,
                    )
                ).strip()
                if not summary:
                    raise ValueError("empty summary")
                job = _current(db, job_id, revision, lock=True)
                if job is None:
                    return False
                chapter = db.get(models.Chapter, source_id)
                if chapter is None:
                    return False
                if chapter.summary:
                    chapter.summary.summary = summary
                    chapter.summary.source_hash = chapter_body_hash(chapter)
                else:
                    chapter.summary = models.ChapterSummary(
                        summary=summary, source_hash=chapter_body_hash(chapter)
                    )
                if chapter.volume_id:
                    enqueue(db, book_id, VOLUME, chapter.volume_id)
                db.commit()  # Retry an embedding without another summary call.
            # Title is part of retrieval text; order and grouping are SQL facts
            # and can change without another embedding or chapter-summary call.
            chapter = db.get(models.Chapter, source_id)
            if chapter is None:
                return False
            text = f"{chapter.title}\n{summary}"
        else:
            operation = "volume_summary"
            volume = db.get(models.Volume, source_id)
            if volume is None:
                return False
            chapters = list(
                db.scalars(
                    select(models.Chapter)
                    .where(
                        models.Chapter.volume_id == source_id,
                        models.Chapter.book_id == book_id,
                        models.Chapter.status == "done",
                    )
                    .options(
                        selectinload(models.Chapter.content),
                        selectinload(models.Chapter.summary),
                    )
                    .order_by(models.Chapter.order_index)
                ).all()
            )
            if any(chapter_hash(ch) and not valid_summary(ch) for ch in chapters):
                raise MissingChapterSummary()
            summaries = [
                f"[{ch.title}] {valid_summary(ch)}"
                for ch in chapters
                if valid_summary(ch)
            ]
            if summaries:
                prompt = prompts.VOLUME_SUMMARY_USER.format(
                    volume_title=volume.title, chapter_summaries="\n".join(summaries)
                )
                db.rollback()
                summary = (
                    await get_llm().chat(
                        prompt,
                        system=prompts.VOLUME_SUMMARY_SYSTEM,
                    )
                ).strip()
                if not summary:
                    raise ValueError("empty volume summary")
            job = _current(db, job_id, revision, lock=True)
            if job is None:
                return False
            volume = db.get(models.Volume, source_id)
            if volume is None:
                return False
            # Keep generated facts separate from the plan, which can include
            # future events. Clearing all completed chapters clears memory too.
            volume.memory_summary = summary
            job.status = "done"
            db.commit()
            return True

        operation = "embedding"
        db.rollback()
        embedding = await get_llm().embed(text)
        if not embedding:
            raise ValueError("empty embedding")
        job = _current(db, job_id, revision, lock=True)
        if job is None:
            return False
        doc_id = _document_id(job)
        metadata: dict[str, Any] = {
            "source_type": source_type,
            "source_id": source_id,
            "revision": revision,
            "source_hash": job.source_hash,
        }
        operation = "delete_vector"
        vector_store.delete_source(book_id, source_type, source_id)
        operation = "upsert_vector"
        vector_store.upsert(book_id, doc_id, embedding, text, metadata)
        _replace_ref(db, job, doc_id)
        job.status = "done"
        job.error = ""
        db.commit()
        return True
    except asyncio.CancelledError:
        db.rollback()
        db.execute(
            update(models.MemorySyncJob)
            .where(
                models.MemorySyncJob.id == job_id,
                models.MemorySyncJob.revision == revision,
                models.MemorySyncJob.status == "running",
            )
            .values(status="pending")
        )
        db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - persist any provider/Chroma failure safely
        db.rollback()
        db.execute(
            update(models.MemorySyncJob)
            .where(
                models.MemorySyncJob.id == job_id,
                models.MemorySyncJob.revision == revision,
            )
            .values(
                status="error",
                error=_safe_error(exc, operation),
                updated_at=func.now(),
            )
        )
        db.commit()
        logger.warning(
            "memory sync failed book_id=%s source_type=%s source_id=%s task_id=%s operation=%s error_type=%s",
            book_id,
            source_type,
            source_id,
            job_id,
            operation,
            type(exc).__name__,
        )
        return False


async def process_chapter_memory(db: Session, chapter: models.Chapter) -> bool:
    book_id, chapter_id, volume_id = chapter.book_id, chapter.id, chapter.volume_id
    try:
        job = find_job(db, book_id, CHAPTER, chapter_id)
        ok = job is not None and await process_job(db, job.id)
        fact_job = find_job(db, book_id, FACTS, chapter_id)
        if fact_job:
            ok = await process_job(db, fact_job.id) and ok
        if volume_id:
            volume_job = find_job(db, book_id, VOLUME, volume_id)
            if volume_job:
                ok = await process_job(db, volume_job.id) and ok
        return ok
    except Exception as exc:  # noqa: BLE001 - content already committed successfully
        db.rollback()
        logger.warning(
            "chapter memory deferred book_id=%s chapter_id=%s error_type=%s",
            book_id,
            chapter_id,
            type(exc).__name__,
        )
        return False


MemoryKey = tuple[str, str]


def _document_id(job: models.MemorySyncJob) -> str:
    return f"mem-{job.id}-{job.revision}"


def _book_memory(
    db: Session,
    book_id: str,
) -> tuple[dict[MemoryKey, models.MemorySyncJob], dict[MemoryKey, str], set[MemoryKey]]:
    jobs = {
        (job.source_type, job.source_id): job
        for job in db.scalars(
            select(models.MemorySyncJob)
            .where(models.MemorySyncJob.book_id == book_id)
            .order_by(models.MemorySyncJob.updated_at.desc(), models.MemorySyncJob.id)
        ).all()
    }
    expected: dict[MemoryKey, str] = {}
    missing_artifacts: set[MemoryKey] = set()
    fact_hashes = {
        chapter_id: digest
        for chapter_id, digest in db.execute(
            select(models.ChapterFacts.chapter_id, models.ChapterFacts.source_hash)
            .join(models.Chapter)
            .where(models.Chapter.book_id == book_id)
        )
    }
    for item in db.scalars(
        select(models.Worldbuilding).where(models.Worldbuilding.book_id == book_id)
    ):
        expected[(WORLD, item.id)] = _hash([item.title, item.md_content])
    chapters_by_volume: dict[str, list[models.Chapter]] = {}
    for chapter in db.scalars(
        select(models.Chapter)
        .where(models.Chapter.book_id == book_id)
        .options(
            selectinload(models.Chapter.content), selectinload(models.Chapter.summary)
        )
        .order_by(models.Chapter.order_index, models.Chapter.id)
    ):
        if fingerprint := chapter_hash(chapter):
            key = (CHAPTER, chapter.id)
            expected[key] = fingerprint
            if (
                (job := jobs.get(key))
                and job.status == "done"
                and not valid_summary(chapter)
            ):
                missing_artifacts.add(key)
            if chapter.volume_id and chapter.status == "done":
                chapters_by_volume.setdefault(chapter.volume_id, []).append(chapter)
            facts_key = (FACTS, chapter.id)
            fact_hash = _fact_hash(chapter)
            expected[facts_key] = fact_hash
            if (
                (job := jobs.get(facts_key))
                and job.status == "done"
                and fact_hashes.get(chapter.id) != fact_hash
            ):
                missing_artifacts.add(facts_key)
    for volume in db.scalars(
        select(models.Volume).where(models.Volume.book_id == book_id)
    ):
        if volume.id in chapters_by_volume or (VOLUME, volume.id) in jobs:
            key = (VOLUME, volume.id)
            completed = chapters_by_volume.get(volume.id, [])
            expected[key] = _volume_hash(volume, completed)
            if (
                (job := jobs.get(key))
                and job.status == "done"
                and completed
                and not volume.memory_summary
            ):
                missing_artifacts.add(key)

    # A copied/deleted Chroma directory can lose vectors even when SQL says the
    # job finished. Detect that drift without calling a model. A temporarily
    # unavailable store must not force a costly rebuild of otherwise ready jobs.
    ready_vectors = {
        key: _document_id(job)
        for key, job in jobs.items()
        if job.status == "done"
        and job.source_hash
        and job.source_type in (WORLD, CHAPTER)
    }
    missing_docs: set[MemoryKey] = set()
    if ready_vectors:
        try:
            present = vector_store.existing_ids(book_id)
            missing_docs = {
                key for key, doc_id in ready_vectors.items() if doc_id not in present
            }
        except Exception as exc:  # noqa: BLE001 - leave jobs intact if storage is unavailable
            logger.warning(
                "memory status check degraded book_id=%s error_type=%s",
                book_id,
                type(exc).__name__,
            )
    keys = set(jobs) | set(expected)
    refs = db.execute(
        select(models.EmbeddingRef.source_type, models.EmbeddingRef.source_id).where(
            models.EmbeddingRef.book_id == book_id
        )
    ).all()
    keys.update((source_type, source_id) for source_type, source_id in refs)
    missing = {
        key
        for key in keys
        if key not in jobs or jobs[key].source_hash != expected.get(key, "")
    }
    missing.update(missing_docs)
    missing.update(missing_artifacts)
    return jobs, expected, missing


def repair_book(db: Session, book_id: str) -> None:
    """Discover legacy/missing indexes and retry failed jobs without body generation."""
    jobs, expected, missing = _book_memory(db, book_id)
    for key in missing | {key for key, job in jobs.items() if job.status == "error"}:
        source_type, source_id = key
        job = jobs.get(key)
        enqueue(
            db,
            book_id,
            source_type,
            source_id,
            removed=key not in expected,
            retry=True,
            force=key in missing and job is not None and job.status == "done",
        )
    db.commit()


def memory_status(db: Session, book_id: str) -> MemoryStatus:
    jobs, _expected, missing = _book_memory(db, book_id)
    counts = {"pending": 0, "running": 0, "error": 0, "done": 0}
    items: list[MemoryJobStatus] = []
    for key, job in jobs.items():
        if key in missing:
            continue
        counts[job.status] += 1
        if job.status != "done":
            items.append(
                {
                    "id": job.id,
                    "source_type": job.source_type,
                    "source_id": job.source_id,
                    "status": job.status,
                    "attempts": job.attempts,
                    "error": job.error,
                    "updated_at": job.updated_at.isoformat(),
                }
            )
    return {
        "pending": counts["pending"],
        "running": counts["running"],
        "failed": counts["error"],
        "ready": counts["done"],
        "missing": len(missing),
        "total": sum(counts.values()) + len(missing),
        "items": items[:100],
    }


async def _worker_loop() -> None:
    from ..database import SessionLocal

    while True:
        try:
            with SessionLocal() as db:
                jobs = list(
                    db.execute(
                        select(models.MemorySyncJob.id, models.MemorySyncJob.book_id)
                        .where(
                            models.MemorySyncJob.status == "pending",
                            ~models.MemorySyncJob.book_id.in_(_suspended),
                        )
                        .order_by(
                            models.MemorySyncJob.source_type,
                            models.MemorySyncJob.updated_at,
                            models.MemorySyncJob.id,
                        )
                        .limit(100)
                    ).all()
                )
            for job_id, book_id in jobs:
                if book_id in _suspended:
                    continue
                with SessionLocal() as db:
                    try:
                        await process_job(db, job_id)
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the durable worker alive
            logger.warning(
                "memory worker iteration failed error_type=%s", type(exc).__name__
            )
        await asyncio.sleep(1)


async def start_memory_worker() -> None:
    global _worker
    from ..database import SessionLocal

    if _worker and not _worker.done():
        return
    _suspended.clear()
    with SessionLocal() as db:
        db.execute(
            update(models.MemorySyncJob)
            .where(models.MemorySyncJob.status == "running")
            .values(status="pending")
        )
        # Legacy missing jobs are exposed by /status and opt-in /repair; startup
        # must not unexpectedly spend model quota rebuilding an entire library.
        db.commit()
        try:
            for book_id in vector_store.list_book_ids():
                if not db.get(models.Book, book_id):
                    vector_store.delete_book(book_id)
        except Exception as exc:  # noqa: BLE001 - Chroma failure must not block startup
            logger.warning(
                "memory orphan cleanup failed error_type=%s", type(exc).__name__
            )
    _worker = asyncio.create_task(_worker_loop())


async def stop_memory_worker() -> None:
    global _worker
    if _worker:
        _worker.cancel()
        with suppress(asyncio.CancelledError):
            await _worker
        _worker = None


async def suspend_book_memory(book_id: str) -> None:
    _suspended.add(book_id)
    tasks = [active.task for active in _active.values() if active.book_id == book_id]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def resume_book_memory(book_id: str) -> None:
    _suspended.discard(book_id)
