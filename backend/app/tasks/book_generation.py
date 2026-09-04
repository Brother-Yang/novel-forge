"""全书生成任务运行时:内存登记、启动、暂停/继续、进程重启恢复。

`type=book` 只补未完成正文;`type=novel` 由 novel_generation 跑完全书编排。
进度、状态、checkpoint 均落库,进程重启后可从 checkpoint 续写。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..agents import pipeline
from ..database import SessionLocal

logger = logging.getLogger(__name__)

_running: dict[str, asyncio.Task[None]] = {}

BLOCKING_STATUSES = ("pending", "running", "paused")
BOOK_LEVEL_TYPES = ("book", "novel", "agent")


class BookBusyError(Exception):
    """同一本书已有未完成的全书级生成任务。"""


class Paused(Exception):
    """任务已被暂停,编排循环应退出且不改写为 error。"""


def find_blocking_task(db: Session, book_id: str) -> models.GenerationTask | None:
    return db.scalars(
        select(models.GenerationTask)
        .where(
            models.GenerationTask.book_id == book_id,
            models.GenerationTask.status.in_(BLOCKING_STATUSES),
            models.GenerationTask.type.in_(BOOK_LEVEL_TYPES),
        )
        .order_by(models.GenerationTask.created_at.desc())
    ).first()


def ensure_book_idle(db: Session, book_id: str) -> None:
    blocking = find_blocking_task(db, book_id)
    if blocking:
        raise BookBusyError("该书已有未完成的生成任务,请先继续/重试或等待结束后再启动")


def ensure_running(db: Session, task: models.GenerationTask) -> None:
    db.refresh(task)
    if task.status != "running":
        raise Paused()


def _pending_chapters(db: Session, book_id: str) -> list[str]:
    rows = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.book_id == book_id,
            models.Chapter.status != "done",
        )
        .order_by(models.Chapter.order_index)
    ).all()
    return [c.id for c in rows]


def reset_empty_drafts(db: Session, book_id: str) -> None:
    """进程中断时可能留下 status=drafting 且正文为空的章,重置后整章重写。"""
    rows = db.scalars(
        select(models.Chapter).where(
            models.Chapter.book_id == book_id,
            models.Chapter.status == "drafting",
        )
    ).all()
    changed = False
    for chapter in rows:
        body = chapter.content.body if chapter.content else ""
        if not body.strip():
            chapter.status = "outline"
            chapter.word_count = 0
            changed = True
    if changed:
        db.commit()


def merge_checkpoint(task: models.GenerationTask, **fields: object) -> str:
    ckpt: dict[str, object] = {}
    if task.checkpoint_json:
        try:
            loaded = json.loads(task.checkpoint_json)
            if isinstance(loaded, dict):
                ckpt = loaded
        except json.JSONDecodeError:
            ckpt = {}
    ckpt.update(fields)
    task.checkpoint_json = json.dumps(ckpt, ensure_ascii=False)
    return task.checkpoint_json


def load_checkpoint(task: models.GenerationTask) -> dict[str, object]:
    if not task.checkpoint_json:
        return {}
    try:
        loaded = json.loads(task.checkpoint_json)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


async def consume_stage(gen: AsyncIterator[tuple[str, str]]) -> None:
    async for ev, data in gen:
        if ev == "error":
            raise RuntimeError(data)


async def run_chapter_bodies(
    db: Session,
    task: models.GenerationTask,
    *,
    words: int = 2000,
    progress_lo: int = 0,
    progress_hi: int = 100,
    mark_done: bool = True,
) -> None:
    reset_empty_drafts(db, task.book_id)
    pending = _pending_chapters(db, task.book_id)
    done_count = (
        db.scalar(
            select(func.count())
            .select_from(models.Chapter)
            .where(
                models.Chapter.book_id == task.book_id,
                models.Chapter.status == "done",
            )
        )
        or 0
    )
    total = done_count + len(pending)
    span = max(0, progress_hi - progress_lo)

    for chapter_id in pending:
        ensure_running(db, task)
        try:
            await consume_stage(pipeline.gen_chapter_body(db, chapter_id, words))
        except Paused:
            raise
        except Exception as exc:
            task.status = "error"
            task.error = f"章节 {chapter_id}: {exc}"
            db.commit()
            logger.exception("chapter %s generation failed", chapter_id)
            return

        done_count += 1
        ratio = done_count / total if total else 1
        task.progress = progress_lo + int(ratio * span) if total else progress_hi
        merge_checkpoint(
            task,
            stage="chapter_bodies",
            chapter_id=chapter_id,
            done_count=done_count,
        )
        db.commit()

    if mark_done:
        ensure_running(db, task)
        task.status = "done"
        task.progress = 100
        merge_checkpoint(task, stage="done")
        db.commit()


async def _run(task_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(models.GenerationTask, task_id)
        if not task:
            return
        try:
            if task.type == "novel":
                from .novel_generation import run_novel_task

                await run_novel_task(db, task)
            elif task.type == "agent":
                from .agent_generation import run_agent_task

                await run_agent_task(db, task)
            else:
                await run_chapter_bodies(db, task)
        except Paused:
            logger.info("generation task %s paused", task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("generation task %s failed", task_id)
            db.refresh(task)
            if task.status == "running":
                task.status = "error"
                task.error = str(exc)
                db.commit()
    finally:
        db.close()
        _ = _running.pop(task_id, None)


def _launch(task_id: str) -> None:
    if task_id in _running and not _running[task_id].done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError(
            "全书生成必须在 FastAPI 的 asyncio 事件循环中启动(接口需为 async)"
        ) from exc
    _running[task_id] = loop.create_task(_run(task_id))


def start_book_generation(db: Session, book_id: str) -> models.GenerationTask:
    ensure_book_idle(db, book_id)
    task = models.GenerationTask(
        book_id=book_id, type="book", status="running", progress=0
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _launch(task.id)
    return task


def pause_task(db: Session, task_id: str) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task and task.status == "running":
        task.status = "paused"
        db.commit()


def resume_task(db: Session, task_id: str) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task and task.status in ("paused", "error"):
        task.status = "running"
        task.error = ""
        db.commit()
        _launch(task.id)


def recover_running_tasks() -> int:
    """进程重启后把库中仍为 running 的任务重新拉起。"""
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(models.GenerationTask).where(models.GenerationTask.status == "running")
        ).all()
        ids = [t.id for t in rows]
    finally:
        db.close()
    for task_id in ids:
        logger.info("recover running generation task %s", task_id)
        _launch(task_id)
    return len(ids)
