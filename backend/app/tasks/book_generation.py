"""全书生成任务运行时:内存登记、启动、暂停/继续、进程重启恢复。

`type=book` 只补未完成正文;`type=novel` 由 novel_generation 跑完全书编排。
进度、状态、checkpoint 均落库,进程重启后可从 checkpoint 续写。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..agents import observability, pipeline
from ..agents.completion import (
    book_chapters,
    chapter_is_complete,
    require_book_complete,
)
from ..agents.errors import GenerationError, log_failure, public_failure
from ..config import settings
from ..database import SessionLocal

logger = logging.getLogger(__name__)

_running: dict[str, asyncio.Task[None]] = {}
_deleting_books: set[str] = set()
_registry_lock = RLock()


@dataclass
class GenerationLease:
    book_id: str
    owner: str
    handle: asyncio.Task[None] | None = None
    cancelled: bool = False


_active_books: dict[str, GenerationLease] = {}

BLOCKING_STATUSES = ("pending", "running", "paused")
BOOK_LEVEL_TYPES = ("book", "novel", "agent")

TaskRunner = Callable[[Session, models.GenerationTask], Awaitable[None]]
_task_runners: dict[str, TaskRunner] = {}


def register_task_runner(task_type: str, runner: TaskRunner) -> None:
    _task_runners[task_type] = runner


def _ensure_task_runners() -> None:
    if "novel" not in _task_runners:
        _ = importlib.import_module(".novel_generation", __package__)
    if "agent" not in _task_runners:
        _ = importlib.import_module(".agent_generation", __package__)


def _as_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


class BookBusyError(GenerationError):
    """同一本书已有未完成的全书级生成任务。"""

    status_code = 409


class GenerationCapacityError(BookBusyError):
    status_code = 429


class Paused(Exception):
    """任务已被暂停,编排循环应退出且不改写为 error。"""


def find_blocking_task(
    db: Session,
    book_id: str,
    *,
    exclude_task_id: str | None = None,
    include_paused: bool = True,
) -> models.GenerationTask | None:
    query = select(models.GenerationTask).where(
        models.GenerationTask.book_id == book_id,
        models.GenerationTask.status.in_(
            BLOCKING_STATUSES if include_paused else ("pending", "running")
        ),
        models.GenerationTask.type.in_(BOOK_LEVEL_TYPES),
    )
    if exclude_task_id is not None:
        query = query.where(models.GenerationTask.id != exclude_task_id)
    return db.scalars(query.order_by(models.GenerationTask.created_at.desc())).first()


def ensure_book_idle(
    db: Session,
    book_id: str,
    *,
    exclude_task_id: str | None = None,
    include_paused: bool = True,
) -> None:
    if book_id in _deleting_books:
        raise BookBusyError("该书正在删除,不能启动生成任务")
    if book_id in _active_books:
        raise BookBusyError("该书正在生成，请等待当前操作结束")
    blocking = find_blocking_task(
        db, book_id, exclude_task_id=exclude_task_id, include_paused=include_paused
    )
    if blocking:
        raise BookBusyError("该书已有未完成的生成任务,请先继续/重试或等待结束后再启动")
    ensure_generation_capacity()


def ensure_generation_capacity() -> None:
    if len(_active_books) >= settings.generation_max_concurrent:
        raise GenerationCapacityError("同时生成的任务已达到上限，请稍后重试")


def claim_manual_generation(db: Session, book_id: str, owner: str) -> GenerationLease:
    with _registry_lock:
        ensure_book_idle(db, book_id, include_paused=False)
        lease = GenerationLease(book_id, owner)
        _active_books[book_id] = lease
        return lease


def release_generation(lease: GenerationLease) -> None:
    with _registry_lock:
        if _active_books.get(lease.book_id) is lease:
            del _active_books[lease.book_id]


def ensure_running(db: Session, task: models.GenerationTask) -> None:
    # A deleted task must stop cleanly rather than trying to refresh or update
    # a row that has already been removed by the book's foreign-key cascade.
    with db.no_autoflush:
        status = db.scalar(
            select(models.GenerationTask.status).where(
                models.GenerationTask.id == task.id
            )
        )
    if status != "running" or task.book_id in _deleting_books:
        raise Paused()
    db.refresh(task)


def _pending_chapters(db: Session, book_id: str) -> list[str]:
    return [c.id for c in book_chapters(db, book_id) if not chapter_is_complete(c)]


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


def merge_checkpoint(task: models.GenerationTask, **fields: object) -> None:
    ckpt: dict[str, object] = {}
    if task.checkpoint_json:
        try:
            loaded = cast(object, json.loads(task.checkpoint_json))
            parsed = _as_object_dict(loaded)
            if parsed is not None:
                ckpt = parsed
        except json.JSONDecodeError:
            ckpt = {}
    ckpt.update(fields)
    task.checkpoint_json = json.dumps(ckpt, ensure_ascii=False)


def load_checkpoint(task: models.GenerationTask) -> dict[str, object]:
    if not task.checkpoint_json:
        return {}
    try:
        loaded = cast(object, json.loads(task.checkpoint_json))
    except json.JSONDecodeError:
        return {}
    return _as_object_dict(loaded) or {}


async def consume_stage(gen: AsyncIterator[tuple[str, str]]) -> None:
    try:
        async for ev, data in gen:
            if ev == "error":
                raise GenerationError(data)
    finally:
        if isinstance(gen, AsyncGenerator):
            await gen.aclose()


def fail_task(
    db: Session,
    exc: Exception,
    *,
    task_id: str,
    book_id: str,
    operation: str,
    chapter_id: str | None = None,
    trace_step: int | None = None,
) -> None:
    """Rollback incomplete writes and persist only a safe error for retry."""
    log_failure(
        logger,
        exc,
        operation=operation,
        book_id=book_id,
        task_id=task_id,
        chapter_id=chapter_id,
    )
    db.rollback()
    current = db.get(models.GenerationTask, task_id, populate_existing=True)
    if current is None or current.status != "running":
        return
    message = public_failure(exc)
    current.status = "error"
    current.error = f"章节 {chapter_id}: {message}" if chapter_id else message
    if trace_step is not None:
        observability.record_step(db, task_id, trace_step, "error", message)
    db.commit()


async def run_chapter_bodies(
    db: Session,
    task: models.GenerationTask,
    *,
    words: int = 2000,
    progress_lo: int = 0,
    progress_hi: int = 100,
    mark_done: bool = True,
) -> None:
    task_id, book_id = task.id, task.book_id
    reset_empty_drafts(db, task.book_id)
    chapters = book_chapters(db, task.book_id)
    pending = [c.id for c in chapters if not chapter_is_complete(c)]
    total = len(chapters)
    done_count = total - len(pending)
    span = max(0, progress_hi - progress_lo)

    for chapter_id in pending:
        ensure_running(db, task)
        try:
            await consume_stage(pipeline.gen_chapter_body(db, chapter_id, words))
            chapter = db.get(models.Chapter, chapter_id)
            if chapter is None or not chapter_is_complete(chapter):
                raise GenerationError("章节正文未有效完成,请重试")
        except Paused:
            raise
        except Exception as exc:  # noqa: BLE001 - persist and report chapter failure
            fail_task(
                db,
                exc,
                task_id=task_id,
                book_id=book_id,
                operation="chapter body",
                chapter_id=chapter_id,
            )
            return

        done_count += 1
        ratio = done_count / total if total else 1
        task.progress = min(99, progress_lo + int(ratio * span))
        merge_checkpoint(
            task,
            stage="chapter_bodies",
            chapter_id=chapter_id,
            done_count=done_count,
        )
        db.commit()

    if mark_done:
        ensure_running(db, task)
        require_book_complete(db, task.book_id)
        task.status = "done"
        task.progress = 100
        merge_checkpoint(task, stage="done")
        db.commit()


def _record_runtime_failure(
    task_id: str, book_id: str, exc: Exception, *, operation: str
) -> None:
    try:
        with SessionLocal() as db:
            fail_task(db, exc, task_id=task_id, book_id=book_id, operation=operation)
    except Exception as persist_error:  # noqa: BLE001 - preserve explicit retry
        log_failure(
            logger,
            persist_error,
            operation="persist task failure",
            book_id=book_id,
            task_id=task_id,
        )


async def _run(task_id: str, lease: GenerationLease, runner: TaskRunner) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(models.GenerationTask, task_id)
            if task is not None and task.status == "running":
                await runner(db, task)
    except Paused:
        logger.info("generation task %s paused", task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - runtime task failure boundary
        _record_runtime_failure(task_id, lease.book_id, exc, operation="task runner")
    finally:
        _ = _running.pop(task_id, None)
        release_generation(lease)


def launch(task_id: str) -> bool:
    """Schedule a committed running task and record a failed launch for retry."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError(
            "全书生成必须在 FastAPI 的 asyncio 事件循环中启动(接口需为 async)"
        ) from exc
    with _registry_lock:
        if task_id in _running and not _running[task_id].done():
            return True
        book_id = ""
        try:
            with SessionLocal() as db:
                task = db.get(models.GenerationTask, task_id)
                if task is None or task.status != "running":
                    return False
                book_id, task_type = task.book_id, task.type
            _ensure_task_runners()
            runner = _task_runners.get(task_type)
            if runner is None and task_type == "book":
                runner = run_chapter_bodies
            if runner is None:
                raise GenerationError(
                    "当前版本不支持此任务类型，请在工作区重新发起生成"
                )
            if book_id in _deleting_books or book_id in _active_books:
                raise BookBusyError("该书正在生成或删除，不能重复执行任务")
            ensure_generation_capacity()
            lease = GenerationLease(book_id, task_id)
            coroutine = _run(task_id, lease, runner)
            try:
                handle = loop.create_task(coroutine)
            except BaseException:
                coroutine.close()
                raise
        except Exception as exc:
            _record_runtime_failure(task_id, book_id, exc, operation="task launch")
            if isinstance(exc, BookBusyError):
                raise
            return False

        _active_books[book_id] = lease
        lease.handle = handle
        _running[task_id] = handle

        def finished(completed: asyncio.Task[None]) -> None:
            # Cancellation before the first coroutine instruction skips finally.
            if _running.get(task_id) is completed:
                _running.pop(task_id, None)
            release_generation(lease)

        handle.add_done_callback(finished)
        return True


def pause_task(db: Session, task_id: str) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task and task.status == "running":
        task.status = "paused"
        db.commit()
        handle = _running.get(task_id)
        if handle is not None:
            handle.get_loop().call_soon_threadsafe(handle.cancel)


def resume_task(db: Session, task_id: str) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task is None or task.status not in ("paused", "error", "running"):
        return
    handle = _running.get(task_id)
    if task.status == "running" and handle is not None and not handle.done():
        return
    # A failed DB write may have prevented a stopped runner from recording its
    # error. An explicit retry can still recover its checkpoint once DB is back.
    ensure_book_idle(db, task.book_id, exclude_task_id=task_id)
    task.status = "running"
    task.error = ""
    db.commit()
    _ = launch(task_id)


def recover_running_tasks() -> int:
    """进程重启后把库中仍为 running 的任务重新拉起。"""
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(models.GenerationTask)
            .where(models.GenerationTask.status == "running")
            .order_by(models.GenerationTask.created_at, models.GenerationTask.id)
        ).all()
        ids = [t.id for t in rows]
    finally:
        db.close()
    recovered = 0
    for task_id in ids:
        logger.info("recover running generation task %s", task_id)
        try:
            recovered += int(launch(task_id))
        except BookBusyError:
            # A legacy database may contain duplicate running tasks or more
            # work than capacity. launch has already recorded a retryable error.
            continue
    return recovered


@contextmanager
def book_deletion(book_id: str) -> Iterator[None]:
    """Keep local start/resume requests out while deletion awaits cancellation."""
    if book_id in _deleting_books:
        raise BookBusyError("该书正在删除")
    _deleting_books.add(book_id)
    try:
        yield
    finally:
        _deleting_books.discard(book_id)


async def cancel_book_tasks(db: Session, book_id: str) -> None:
    """Drain local writers, then pause persisted work before deleting its book.

    Paused status also prevents restart recovery from resurrecting the work if
    vector cleanup fails and the book must remain available for a delete retry.
    """
    ids = list(
        db.scalars(
            select(models.GenerationTask.id).where(
                models.GenerationTask.book_id == book_id
            )
        )
    )
    running = [
        handle for task_id in ids if (handle := _running.get(task_id)) is not None
    ]
    manual = _active_books.get(book_id)
    if manual is not None:
        manual.cancelled = True
        if manual.handle is not None and manual.handle not in running:
            running.append(manual.handle)
        elif manual.handle is None:
            release_generation(manual)
    for handle in running:
        handle.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    # A task cancelled before its coroutine starts never reaches _run.finally.
    for task_id in ids:
        handle = _running.get(task_id)
        if handle is not None and handle.done():
            _running.pop(task_id, None)
    db.expire_all()
    tasks = db.scalars(
        select(models.GenerationTask).where(models.GenerationTask.id.in_(ids))
    ).all()
    for task in tasks:
        if task.status in ("pending", "running"):
            task.status = "paused"
    db.commit()
    logger.info(
        "stopped %s local generation tasks before deleting book %s",
        len(running),
        book_id,
    )


async def stop_all_generation() -> None:
    """Drain writers before memory-worker shutdown and releasing the OS locks.

    Running SQL statuses are intentionally retained for recovery on next start.
    """
    for lease in _active_books.values():
        lease.cancelled = True
    handles = {
        lease.handle for lease in _active_books.values() if lease.handle is not None
    }
    handles.update(_running.values())
    for handle in handles:
        handle.cancel()
    if handles:
        await asyncio.gather(*handles, return_exceptions=True)
    _running.clear()
    _active_books.clear()
