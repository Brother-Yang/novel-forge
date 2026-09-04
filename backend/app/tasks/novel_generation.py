"""全书自动编排:planner → 世界观 → 人物 → 大纲 → 章纲 → 正文。

跳过已落库的层,失败/暂停后从跳过规则续跑,不新建任务。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..agents import pipeline, planner
from . import book_generation as runtime

logger = logging.getLogger(__name__)

PROGRESS = {
    "planner": 5,
    "worldbuilding": 12,
    "characters": 20,
    "outline": 30,
    "chapter_outlines": 45,
    "chapter_bodies": 45,
}


def start_novel_generation(db: Session, book_id: str) -> models.GenerationTask:
    runtime.ensure_book_idle(db, book_id)
    task = models.GenerationTask(
        book_id=book_id,
        type="novel",
        status="running",
        progress=0,
        checkpoint_json="{}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    runtime._launch(task.id)
    return task


def _count(db: Session, model: type[object], book_id: str) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(model).where(model.book_id == book_id)  # type: ignore[attr-defined]
        )
        or 0
    )


def _volume_chapter_count(db: Session, volume_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(models.Chapter)
            .where(models.Chapter.volume_id == volume_id)
        )
        or 0
    )


async def _ensure_plan(db: Session, task: models.GenerationTask) -> planner.NovelPlan:
    book = db.get(models.Book, task.book_id)
    if not book:
        raise RuntimeError("书籍不存在")
    ckpt = runtime.load_checkpoint(task)
    existing = planner.plan_from_checkpoint(
        ckpt,
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        intro=book.intro,
        key_elements=book.key_elements,
    )
    if existing:
        logger.info("novel task %s skip planner (checkpoint has scale)", task.id)
        return existing

    runtime.ensure_running(db, task)
    runtime.merge_checkpoint(task, stage="planner")
    db.commit()
    brief = book.brief or book.intro
    user_title = "" if planner.is_placeholder_title(book.title, book.brief) else book.title
    plan = await planner.plan_novel(
        brief,
        title=user_title,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
    )
    planner.apply_plan_to_book(book, plan)
    runtime.merge_checkpoint(task, stage="planner", **plan.to_checkpoint())
    task.progress = PROGRESS["planner"]
    db.commit()
    return plan


async def run_novel_task(db: Session, task: models.GenerationTask) -> None:
    try:
        plan = await _ensure_plan(db, task)
        runtime.ensure_running(db, task)

        if _count(db, models.Worldbuilding, task.book_id) == 0:
            runtime.merge_checkpoint(task, stage="worldbuilding")
            db.commit()
            await runtime.consume_stage(pipeline.gen_worldbuilding(db, task.book_id))
        else:
            logger.info("novel task %s skip worldbuilding", task.id)
        task.progress = PROGRESS["worldbuilding"]
        runtime.merge_checkpoint(task, stage="worldbuilding", **plan.to_checkpoint())
        db.commit()
        runtime.ensure_running(db, task)

        if _count(db, models.Character, task.book_id) == 0:
            runtime.merge_checkpoint(task, stage="characters")
            db.commit()
            await runtime.consume_stage(
                pipeline.gen_characters(db, task.book_id, plan.character_count)
            )
        else:
            logger.info("novel task %s skip characters", task.id)
        task.progress = PROGRESS["characters"]
        runtime.merge_checkpoint(task, stage="characters", **plan.to_checkpoint())
        db.commit()
        runtime.ensure_running(db, task)

        if _count(db, models.Volume, task.book_id) == 0:
            runtime.merge_checkpoint(task, stage="outline")
            db.commit()
            await runtime.consume_stage(
                pipeline.gen_outline(db, task.book_id, plan.volume_count)
            )
        else:
            logger.info("novel task %s skip outline", task.id)
        task.progress = PROGRESS["outline"]
        runtime.merge_checkpoint(task, stage="outline", **plan.to_checkpoint())
        db.commit()
        runtime.ensure_running(db, task)

        volumes = db.scalars(
            select(models.Volume)
            .where(models.Volume.book_id == task.book_id)
            .order_by(models.Volume.order_index)
        ).all()
        runtime.merge_checkpoint(task, stage="chapter_outlines", **plan.to_checkpoint())
        db.commit()
        for volume in volumes:
            runtime.ensure_running(db, task)
            if _volume_chapter_count(db, volume.id) > 0:
                logger.info(
                    "novel task %s skip chapter outlines for volume %s",
                    task.id,
                    volume.id,
                )
                continue
            runtime.merge_checkpoint(
                task,
                stage="chapter_outlines",
                volume_id=volume.id,
                **plan.to_checkpoint(),
            )
            db.commit()
            await runtime.consume_stage(
                pipeline.gen_chapter_outlines(
                    db, volume.id, plan.chapters_per_volume
                )
            )
        task.progress = PROGRESS["chapter_outlines"]
        runtime.merge_checkpoint(task, stage="chapter_outlines", **plan.to_checkpoint())
        db.commit()
        runtime.ensure_running(db, task)

        await runtime.run_chapter_bodies(
            db,
            task,
            words=plan.words_per_chapter,
            progress_lo=PROGRESS["chapter_bodies"],
            progress_hi=100,
            mark_done=True,
        )
    except runtime.Paused:
        logger.info("novel task %s paused", task.id)
        return
    except Exception as exc:
        logger.exception("novel task %s failed", task.id)
        db.refresh(task)
        if task.status == "running":
            task.status = "error"
            task.error = str(exc)
            db.commit()
