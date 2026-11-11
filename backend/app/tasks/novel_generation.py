"""全书自动编排:planner → 世界观 → 人物 → 大纲 → 章纲 → 正文。

每阶段在同一事务保存内容和检查点；旧版或手工创建的记录仅补缺，不覆盖已有内容。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..agents import pipeline, planner, stages
from ..agents.errors import GenerationError
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


async def _ensure_plan(db: Session, task: models.GenerationTask) -> planner.NovelPlan:
    book = db.get(models.Book, task.book_id)
    if not book:
        raise GenerationError("书籍不存在")
    ckpt = runtime.load_checkpoint(task)
    existing = planner.plan_from_checkpoint(
        ckpt,
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        intro=book.intro,
        key_elements=book.key_elements,
        target_words=book.target_words,
    )
    if existing:
        logger.info("novel task %s skip planner (checkpoint has scale)", task.id)
        return existing

    runtime.ensure_running(db, task)
    runtime.merge_checkpoint(task, stage="planner")
    db.commit()
    brief = book.brief or book.intro
    user_title = (
        "" if planner.is_placeholder_title(book.title, book.brief) else book.title
    )
    plan = await planner.plan_novel(
        brief,
        title=user_title,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
    )
    runtime.ensure_running(db, task)
    planner.apply_plan_to_book(book, plan)
    runtime.merge_checkpoint(task, stage="planner", **plan.to_checkpoint())
    task.progress = PROGRESS["planner"]
    db.commit()
    return plan


def _stage_records(task: models.GenerationTask) -> dict[str, object]:
    raw = runtime.load_checkpoint(task).get("generation_stages")
    return cast(dict[str, object], raw) if isinstance(raw, dict) else {}


def _prior_counts(task: models.GenerationTask, key: str) -> dict[str, int]:
    raw = _stage_records(task).get(key)
    if not isinstance(raw, dict):
        return {}
    return {
        name: count
        for name, count in cast(dict[str, object], raw).items()
        if type(count) is int and count >= 0
    }


async def _run_stage(
    db: Session,
    task: models.GenerationTask,
    stage: str,
    generate: Callable[[stages.Recovery], AsyncIterator[pipeline.Event]],
    *,
    volume_id: str | None = None,
) -> None:
    runtime.ensure_running(db, task)
    runtime.merge_checkpoint(task, stage=stage, volume_id=volume_id)
    db.commit()
    key = f"{stage}:{volume_id}" if volume_id else stage
    committed = False

    def record(counts: dict[str, int]) -> None:
        nonlocal committed
        runtime.ensure_running(db, task)
        records = _stage_records(task)
        records[key] = counts
        runtime.merge_checkpoint(task, generation_stages=records)
        if volume_id is None:
            task.progress = max(task.progress, PROGRESS[stage])
        committed = True

    await runtime.consume_stage(
        generate(stages.Recovery(_prior_counts(task, key), record))
    )
    if not committed:
        raise GenerationError("生成阶段未完整保存，请重试")


async def run_novel_task(db: Session, task: models.GenerationTask) -> None:
    task_id, book_id = task.id, task.book_id
    try:
        plan = await _ensure_plan(db, task)
        await _run_stage(
            db,
            task,
            "worldbuilding",
            lambda recovery: pipeline.gen_worldbuilding(db, book_id, recovery=recovery),
        )
        await _run_stage(
            db,
            task,
            "characters",
            lambda recovery: pipeline.gen_characters(
                db, book_id, plan.character_count, recovery=recovery
            ),
        )
        await _run_stage(
            db,
            task,
            "outline",
            lambda recovery: pipeline.gen_outline(
                db, book_id, plan.volume_count, recovery=recovery
            ),
        )
        volumes = db.scalars(
            select(models.Volume)
            .where(models.Volume.book_id == book_id)
            .order_by(models.Volume.order_index, models.Volume.id)
        ).all()
        for volume in volumes:
            await _run_stage(
                db,
                task,
                "chapter_outlines",
                lambda recovery, volume_id=volume.id: pipeline.gen_chapter_outlines(
                    db, volume_id, plan.chapters_per_volume, recovery=recovery
                ),
                volume_id=volume.id,
            )
        runtime.ensure_running(db, task)
        task.progress = max(task.progress, PROGRESS["chapter_outlines"])
        runtime.merge_checkpoint(task, stage="chapter_bodies", volume_id=None)
        db.commit()

        await runtime.run_chapter_bodies(
            db,
            task,
            words=plan.words_per_chapter,
            progress_lo=PROGRESS["chapter_bodies"],
            progress_hi=100,
            mark_done=True,
        )
    except runtime.Paused:
        db.rollback()
        logger.info("novel task %s paused", task_id)
        return
    except Exception as exc:  # noqa: BLE001 - persist failure for task retry
        runtime.fail_task(
            db, exc, task_id=task_id, book_id=book_id, operation="novel pipeline"
        )


runtime.register_task_runner("novel", run_novel_task)
