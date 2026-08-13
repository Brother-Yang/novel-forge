"""全书批量正文生成任务:逐章生成,支持暂停/继续/重试与断点续写。

进度、状态、checkpoint 均落库(generation_tasks),进程重启后可从 checkpoint 续写。
运行中的 asyncio 任务登记在内存 _running 中,避免重复启动。
"""

import asyncio
import json

from sqlalchemy import select

from .. import models
from ..agents import pipeline
from ..database import SessionLocal

_running: dict[int, asyncio.Task] = {}


def _pending_chapters(db, book_id: int) -> list[int]:
    rows = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.book_id == book_id,
            models.Chapter.status != "done",
        )
        .order_by(models.Chapter.order_index)
    ).all()
    return [c.id for c in rows]


async def _run(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.get(models.GenerationTask, task_id)
        if not task:
            return
        pending = _pending_chapters(db, task.book_id)
        done_count = (
            db.query(models.Chapter)
            .filter(
                models.Chapter.book_id == task.book_id,
                models.Chapter.status == "done",
            )
            .count()
        )
        total = done_count + len(pending)

        for chapter_id in pending:
            db.refresh(task)
            if task.status != "running":  # 被暂停
                return

            try:
                async for _ev, _data in pipeline.gen_chapter_body(db, chapter_id):
                    pass
            except Exception as exc:
                task.status = "error"
                task.error = f"章节 {chapter_id}: {exc}"
                db.commit()
                return

            done_count += 1
            task.progress = int(done_count / total * 100) if total else 100
            task.checkpoint_json = json.dumps(
                {"last_chapter_id": chapter_id, "done_count": done_count}
            )
            db.commit()

        task.status = "done"
        task.progress = 100
        db.commit()
    finally:
        db.close()
        _running.pop(task_id, None)


def _launch(task_id: int) -> None:
    if task_id in _running and not _running[task_id].done():
        return
    _running[task_id] = asyncio.create_task(_run(task_id))


def start_book_generation(db, book_id: int) -> models.GenerationTask:
    task = models.GenerationTask(
        book_id=book_id, type="book", status="running", progress=0
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _launch(task.id)
    return task


def pause_task(db, task_id: int) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task and task.status == "running":
        task.status = "paused"
        db.commit()


def resume_task(db, task_id: int) -> None:
    task = db.get(models.GenerationTask, task_id)
    if task and task.status in ("paused", "error"):
        task.status = "running"
        task.error = ""
        db.commit()
        _launch(task.id)
