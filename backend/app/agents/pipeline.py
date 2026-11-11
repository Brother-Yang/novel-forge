"""分层生成 Agent。

每个函数是异步生成器,产出 (event, data) 元组,便于 SSE 转发:
  - token: 流式文本片段
  - log:   状态提示
  - done:  完成
  - error: 出错
生成结束后负责把结构化结果落库(卷/章/人物/设定/摘要)。
"""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from .. import models
from ..llm import get_llm
from ..memory import context as memory
from ..memory import sync as memory_sync
from . import chapter_plans, chapter_writes, planner, prompts, stages
from .chapter_versions import ChapterContentConflict
from .errors import GenerationError, log_failure

logger = logging.getLogger(__name__)
Event = tuple[str, str]


def _esc(value: str) -> str:
    return value.replace("{", "{{").replace("}", "}}")


def _book_prompt(book: models.Book) -> dict[str, str]:
    """各层生成提示词共用的书籍字段;创作想法始终带上,避免只靠空简介胡编。"""
    idea = (book.brief or book.intro or "").strip()
    return {
        "title": _esc(book.title),
        "genre": _esc(book.genre or "由你根据创作想法决定"),
        "tone": _esc(book.tone or "由你根据创作想法决定"),
        "intro": _esc((book.intro or "").strip() or idea),
        "brief": _esc(idea),
        "target_words": str(book.target_words),
        "key_elements": _esc(planner.format_key_elements(book.key_elements)),
    }


async def _stream(system: str, user: str) -> AsyncGenerator[Event]:
    stream = get_llm().chat_stream(user, system=system)
    full: list[str] = []
    try:
        async for chunk in stream:
            full.append(chunk)
            yield ("token", chunk)
    finally:
        if isinstance(stream, AsyncGenerator):
            await stream.aclose()
    yield ("full", "".join(full))


# ---------- 1. 世界观 ----------
def _recovery_context(
    existing: list[stages.Section],
    label: str,
    *,
    limit: int = 2400,
) -> str:
    if not existing:
        return ""
    text = "\n".join(f"- {item.title}：{item.body[:200]}" for item in existing)
    return f"\n以下是已保存的{label}，用来保持连续性，请勿重新输出：\n{text[:limit]}\n"


def _remaining_numbers(
    existing: list[stages.Section],
    target: int,
    unit: str,
) -> list[int]:
    # 旧版逐条提交的记录只可能是前缀。已有数字编号若明确指向其他位置，
    # 无法可靠判断旧稿槽位；暂停而不是生成可能重复的分卷或章节。
    if any(
        (number := stages.ordinal(item.title, unit)) is not None and number != index
        for index, item in enumerate(existing, 1)
    ):
        raise stages.InvalidStage(
            f"已有{unit}编号不连续，无法安全补齐；请核对目录后重试，已有内容已保留"
        )
    return list(range(len(existing) + 1, target + 1))


def _retry_message(exc: stages.InvalidStage) -> str:
    return f"{exc}。本阶段未写入新内容，请修正或重试。"


async def gen_worldbuilding(
    db: Session,
    book_id: str,
    *,
    recovery: stages.Recovery | None = None,
) -> AsyncIterator[Event]:
    book = db.get(models.Book, book_id)
    if not book:
        yield ("error", "书籍不存在")
        return
    existing = (
        list(
            db.scalars(
                select(models.Worldbuilding)
                .where(models.Worldbuilding.book_id == book_id)
                .order_by(models.Worldbuilding.id)
            )
        )
        if recovery
        else []
    )
    saved = [stages.Section(item.title, item.md_content) for item in existing]
    try:
        stages.validate_existing(saved, "世界观设定")
        previous = recovery.previous.get("worldbuilding", 0) if recovery else 0
        target = max(stages.WORLD_MIN, min(previous, stages.WORLD_MAX))
        if recovery and len(existing) >= target:
            stages.commit(db, recovery, worldbuilding=len(existing))
            yield ("skipped", f"已保存 {len(existing)} 条完整设定")
            yield ("done", "")
            return

        minimum = target - len(existing)
        maximum = minimum if existing or target > stages.WORLD_MIN else stages.WORLD_MAX
        quantity = str(minimum) if minimum == maximum else f"{minimum}-{maximum}"
        user = prompts.WORLDBUILDING_USER.format(
            **_book_prompt(book),
            count=quantity,
            existing=_recovery_context(saved, "设定"),
        )
        full = ""
        async with aclosing(_stream(prompts.WORLDBUILDING_SYSTEM, user)) as stream:
            async for ev, data in stream:
                if ev == "full":
                    full = data
                else:
                    yield ev, data
        sections = stages.parse_sections(
            full,
            level=2,
            minimum=minimum,
            maximum=maximum,
            label="世界观设定",
        )
        stages.require_distinct(sections, saved, "设定")
    except stages.InvalidStage as exc:
        yield ("error", _retry_message(exc))
        return

    items = [
        models.Worldbuilding(
            book_id=book_id, category="general", title=item.title, md_content=item.body
        )
        for item in sections
    ]
    db.add_all(items)

    def enqueue_memory() -> None:
        for item in items:
            memory_sync.enqueue(db, book_id, memory_sync.WORLD, item.id)

    stages.commit(
        db,
        recovery,
        before_commit=enqueue_memory,
        worldbuilding=len(existing) + len(items),
    )
    for item in items:
        try:
            indexed = await memory_sync.process_source(
                db, book_id, memory_sync.WORLD, item.id
            )
        except Exception as exc:  # noqa: BLE001 - durable outbox retries indexing
            db.rollback()
            logger.warning(
                "worldbuilding index failed book=%s item=%s (%s)",
                book_id,
                item.id,
                type(exc).__name__,
            )
            indexed = False
        if indexed is False:
            yield ("log", f"设定《{item.title}》已保存，记忆待修复")
    yield ("log", f"已生成 {len(items)} 条世界观设定")
    yield ("done", "")


# ---------- 2. 人物 ----------
def _world_digest(db: Session, book_id: str, limit: int = 1500) -> str:
    items = db.scalars(
        select(models.Worldbuilding).where(models.Worldbuilding.book_id == book_id)
    ).all()
    text = "\n".join(f"{i.title}: {i.md_content}" for i in items)
    return text[:limit]


async def gen_characters(
    db: Session,
    book_id: str,
    count: int = 5,
    *,
    recovery: stages.Recovery | None = None,
) -> AsyncIterator[Event]:
    if not 1 <= count <= planner.CHARACTER_MAX:
        yield ("error", f"人物数量必须在 1 到 {planner.CHARACTER_MAX} 之间")
        return
    book = db.get(models.Book, book_id)
    if not book:
        yield ("error", "书籍不存在")
        return
    existing = (
        list(
            db.scalars(
                select(models.Character)
                .where(models.Character.book_id == book_id)
                .order_by(models.Character.order_index, models.Character.id)
            )
        )
        if recovery
        else []
    )
    saved = [stages.Section(item.name, item.md_content) for item in existing]
    try:
        stages.validate_existing(saved, "人物", maximum=count)
        missing = count - len(existing)
        if recovery and missing == 0:
            stages.commit(db, recovery, characters=len(existing))
            yield ("skipped", f"已保存 {len(existing)} 个完整人物")
            yield ("done", "")
            return
        user = prompts.CHARACTERS_USER.format(
            **_book_prompt(book),
            world=_world_digest(db, book_id),
            count=missing,
            existing=_recovery_context(saved, "人物卡"),
        )
        full = ""
        async with aclosing(_stream(prompts.CHARACTERS_SYSTEM, user)) as stream:
            async for ev, data in stream:
                if ev == "full":
                    full = data
                else:
                    yield ev, data
        sections = stages.parse_sections(
            full,
            level=1,
            minimum=missing,
            maximum=missing,
            label="人物卡",
            title_limit=128,
        )
        stages.validate_character_fields(sections)
        stages.require_distinct(sections, saved, "人物")
    except stages.InvalidStage as exc:
        yield ("error", _retry_message(exc))
        return

    base = db.scalar(
        select(func.max(models.Character.order_index)).where(
            models.Character.book_id == book_id
        )
    )
    order = (base if base is not None else -1) + 1
    items = [
        models.Character(
            book_id=book_id,
            name=item.title,
            order_index=order + index,
            md_content=f"# {item.title}\n{item.body}",
            state_json="{}",
        )
        for index, item in enumerate(sections)
    ]
    db.add_all(items)
    stages.commit(db, recovery, characters=len(existing) + len(items))
    yield ("log", f"已生成 {len(items)} 个人物（各一份 Markdown）")
    yield ("done", "")


# ---------- 3. 全书大纲 + 剧情线程 ----------
def _character_digest(db: Session, book_id: str, limit: int = 1200) -> str:
    chars = db.scalars(
        select(models.Character).where(models.Character.book_id == book_id)
    ).all()
    return "\n".join(f"- {c.name}" for c in chars)[:limit]


async def gen_outline(
    db: Session,
    book_id: str,
    volume_count: int = 3,
    *,
    recovery: stages.Recovery | None = None,
) -> AsyncIterator[Event]:
    if not 1 <= volume_count <= planner.VOLUME_MAX:
        yield ("error", f"卷数必须在 1 到 {planner.VOLUME_MAX} 之间")
        return
    book = db.get(models.Book, book_id)
    if not book:
        yield ("error", "书籍不存在")
        return
    existing_volumes = (
        list(
            db.scalars(
                select(models.Volume)
                .where(models.Volume.book_id == book_id)
                .order_by(models.Volume.order_index, models.Volume.id)
            )
        )
        if recovery
        else []
    )
    existing_threads = (
        list(
            db.scalars(
                select(models.PlotThread)
                .where(models.PlotThread.book_id == book_id)
                .order_by(models.PlotThread.id)
            )
        )
        if recovery
        else []
    )
    saved_volumes = [stages.Section(row.title, row.summary) for row in existing_volumes]
    saved_threads = [
        stages.Section(row.name, row.description) for row in existing_threads
    ]

    try:
        stages.validate_existing(saved_volumes, "分卷大纲", maximum=volume_count)
        stages.validate_existing(saved_threads, "剧情线程")
        volume_numbers = (
            _remaining_numbers(saved_volumes, volume_count, "卷")
            if len(saved_volumes) < volume_count
            else []
        )
        previous_count = recovery.previous.get("threads", 0) if recovery else 0
        missing_threads = max(1, previous_count) - len(saved_threads)
        thread_count = (
            max(0, missing_threads) if previous_count or saved_threads else None
        )
        if not volume_numbers and thread_count == 0:
            stages.commit(
                db,
                recovery,
                volumes=len(existing_volumes),
                threads=len(existing_threads),
            )
            yield (
                "skipped",
                f"已保存 {len(existing_volumes)} 卷和 {len(existing_threads)} 条剧情线程",
            )
            yield ("done", "")
            return

        volumes_instruction = ""
        if volume_numbers:
            numbers = "、".join(str(number) for number in volume_numbers)
            volumes_instruction = (
                f"输出 {len(volume_numbers)} 项分卷大纲，只写第 {numbers} 卷，"
                "按顺序，每卷以「## 第N卷 卷名」开头，其下写 150-300 字梗概。"
                "若包含全书最后一卷，最后一卷梗概必须写明主线冲突的收束与全书结局。"
            )
        thread_instruction = ""
        if thread_count != 0:
            thread_quantity = "至少 1" if thread_count is None else str(thread_count)
            thread_instruction = (
                f"输出 {thread_quantity} 条剧情线程，以「# 剧情线程」开头；"
                "其下每行一条，格式「- 线程名 | 简要描述」。若同时输出分卷大纲，线程小节放在最后。"
            )
        user = prompts.OUTLINE_USER.format(
            **_book_prompt(book),
            world=_world_digest(db, book_id),
            characters=_character_digest(db, book_id),
            volume_count=volume_count,
            instructions="\n".join(
                filter(None, (volumes_instruction, thread_instruction))
            ),
            existing=_recovery_context(saved_volumes, "分卷大纲")
            + _recovery_context(saved_threads, "剧情线程"),
        )
        full = ""
        async with aclosing(_stream(prompts.OUTLINE_SYSTEM, user)) as stream:
            async for ev, data in stream:
                if ev == "full":
                    full = data
                else:
                    yield ev, data
        volume_sections, thread_sections = stages.parse_outline(
            full, volume_numbers, thread_count
        )
        stages.require_distinct(volume_sections, saved_volumes, "分卷")
        stages.require_distinct(thread_sections, saved_threads, "剧情线程")
    except stages.InvalidStage as exc:
        yield ("error", _retry_message(exc))
        return

    base = db.scalar(
        select(func.max(models.Volume.order_index)).where(
            models.Volume.book_id == book_id
        )
    )
    order = (base if base is not None else -1) + 1
    volumes = [
        models.Volume(
            book_id=book_id,
            title=item.title,
            order_index=order + index,
            summary=item.body,
        )
        for index, item in enumerate(volume_sections)
    ]
    threads = [
        models.PlotThread(
            book_id=book_id, name=item.title, description=item.body, status="open"
        )
        for item in thread_sections
    ]
    db.add_all([*volumes, *threads])
    stages.commit(
        db,
        recovery,
        volumes=len(existing_volumes) + len(volumes),
        threads=len(existing_threads) + len(threads),
    )
    yield ("log", f"已生成 {len(volumes)} 卷、{len(threads)} 条剧情线程")
    yield ("done", "")


# ---------- 4. 章节大纲 ----------
async def gen_chapter_outlines(
    db: Session,
    volume_id: str,
    chapter_count: int = 10,
    *,
    recovery: stages.Recovery | None = None,
) -> AsyncIterator[Event]:
    if not 1 <= chapter_count <= planner.CHAPTERS_PER_VOLUME_MAX:
        yield ("error", f"每卷章数必须在 1 到 {planner.CHAPTERS_PER_VOLUME_MAX} 之间")
        return
    volume = db.get(models.Volume, volume_id)
    if not volume:
        yield ("error", "卷不存在")
        return
    book = db.get(models.Book, volume.book_id)
    if not book:
        yield ("error", "书籍不存在")
        return
    existing = (
        chapter_plans.ordered_volume_chapters(db, book.id, volume_id)
        if recovery
        else []
    )
    saved = [stages.Section(item.title, item.outline) for item in existing]
    try:
        stages.validate_existing(saved, "章节细纲", maximum=chapter_count)
        if recovery and len(saved) == chapter_count:
            stages.commit(db, recovery, chapters=len(existing))
            yield ("skipped", f"《{volume.title}》已有 {len(existing)} 章完整细纲")
            yield ("done", "")
            return
        numbers = _remaining_numbers(saved, chapter_count, "章")
        max_volume_order = db.scalar(
            select(func.max(models.Volume.order_index)).where(
                models.Volume.book_id == book.id
            )
        )
        is_final_volume = (
            max_volume_order is not None and volume.order_index >= max_volume_order
        )
        user = prompts.CHAPTER_OUTLINE_USER.format(
            **_book_prompt(book),
            volume_title=_esc(volume.title),
            volume_summary=_esc(volume.summary),
            chapter_count=len(numbers),
            chapter_numbers="、".join(str(number) for number in numbers),
            existing=_recovery_context(saved, "章节细纲"),
            ending_hint=(
                prompts.CHAPTER_OUTLINE_ENDING_HINT if is_final_volume else ""
            ),
        )
        full = ""
        async with aclosing(_stream(prompts.CHAPTER_OUTLINE_SYSTEM, user)) as stream:
            async for ev, data in stream:
                if ev == "full":
                    full = data
                else:
                    yield ev, data
        sections = stages.parse_sections(
            full,
            level=2,
            minimum=len(numbers),
            maximum=len(numbers),
            label="章节细纲",
        )
        stages.validate_ordinals(sections, numbers, "章")
        stages.require_distinct(sections, saved, "章节")
        chapter_plans.stage_new_outlines(
            db,
            volume,
            sections,
            retained_ids={chapter.id for chapter in existing} if recovery else None,
        )
    except stages.InvalidStage as exc:
        yield ("error", _retry_message(exc))
        return

    stages.commit(
        db,
        recovery,
        chapters=len(existing) + len(sections),
    )
    yield ("log", f"已为《{volume.title}》生成 {len(sections)} 章大纲")
    yield ("done", "")


# ---------- 5+6. 章节正文 + 一致性回写 ----------
async def gen_chapter_body(
    db: Session,
    chapter_id: str,
    words: int = 2000,
    is_ending: bool | None = None,
    *,
    expected_version: str | None = None,
    require_revision_approval: bool = False,
    unfinished_only: bool = False,
) -> AsyncGenerator[Event]:
    try:
        draft = chapter_writes.begin_draft(
            db,
            chapter_id,
            expected_version=expected_version,
            require_revision_approval=require_revision_approval,
            unfinished_only=unfinished_only,
        )
    except ChapterContentConflict:
        yield ("content_conflict", "")
        yield ("error", "章节正文已在其他窗口更新，请重新加载后生成")
        return
    except GenerationError as exc:
        yield ("error", str(exc))
        return
    if draft is None:
        yield ("skipped", "章节正文已完成，跳过生成")
        yield ("done", "")
        return
    chapter, expected, book_title = draft

    result_saved = False
    try:
        if is_ending is None:
            last_chapter_id = db.scalar(
                select(models.Chapter.id)
                .where(models.Chapter.book_id == expected.book_id)
                .order_by(models.Chapter.order_index.desc(), models.Chapter.id.desc())
                .limit(1)
            )
            is_ending = expected.chapter_id == last_chapter_id

        try:
            ctx = await memory.build_chapter_context(db, chapter)
        except ObjectDeletedError:
            db.rollback()
            yield ("error", chapter_writes.DELETED_DURING_GENERATION)
            return
        if ctx.endswith(memory.MEMORY_DEGRADED_BLOCK):
            yield ("log", memory.MEMORY_DEGRADED_NOTE)
            ctx = ctx.removesuffix(memory.MEMORY_DEGRADED_BLOCK)
        user = prompts.CHAPTER_BODY_USER.format(
            title=book_title,
            chapter_title=expected.title,
            context=ctx,
            words=words,
            ending_note=(prompts.CHAPTER_BODY_ENDING_NOTE if is_ending else ""),
        )
        db.rollback()

        full = ""
        async with aclosing(_stream(prompts.CHAPTER_BODY_SYSTEM, user)) as stream:
            async for ev, data in stream:
                if ev == "full":
                    full = data
                else:
                    yield ev, data

        body = full.strip()
        try:
            chapter = chapter_writes.save_result(db, expected, body)
        except GenerationError as exc:
            yield ("error", str(exc))
            return
        result_saved = True
        if not body:
            yield ("error", "模型未生成有效章节正文,请重试")
            return
    finally:
        if not result_saved:
            try:
                chapter_writes.abandon_draft(db, expected)
            except Exception as exc:  # noqa: BLE001 - preserve the original outcome
                log_failure(
                    logger,
                    exc,
                    operation="restore unfinished chapter status",
                    book_id=expected.book_id,
                    chapter_id=expected.chapter_id,
                )

    # 一致性回写:生成章摘要并写入记忆库
    yield ("log", "正在提取章摘要并回写记忆...")
    if not await memory_sync.process_chapter_memory(db, chapter):
        yield ("log", "正文已保存，部分记忆待修复；可在书籍工作区重试。")

    yield ("done", "")


# ---------- Replanning(智能体发现剧情走偏时主动触发) ----------
async def replan_volume_chapters(
    db: Session, volume_id: str, reason: str
) -> AsyncIterator[Event]:
    """只重新规划该卷【尚未完成正文】的章节细纲,已完成正文的章节与分卷大纲
    (Volume.summary)本身不受影响,避免推翻已经写好的内容。"""
    volume = db.get(models.Volume, volume_id)
    if not volume:
        yield ("error", "卷不存在")
        return
    book = db.get(models.Book, volume.book_id)
    if not book:
        yield ("error", "书籍不存在")
        return

    book_id, volume_title = book.id, volume.title
    chapters = chapter_plans.ordered_volume_chapters(db, book_id, volume_id)
    targets = chapter_plans.replan_targets(chapters)
    if not targets:
        yield ("log", f"《{volume.title}》所有章节已完成正文,无需重新规划")
        yield ("done", "")
        return

    summaries = []
    for ch in chapters:
        if ch.status != "done":
            continue
        summary = memory_sync.valid_summary(ch)
        if summary:
            summaries.append(f"[{ch.title}] {summary}")
    completed_summaries = "\n".join(summaries) or "(该卷暂无已完成章节)"

    user = prompts.CHAPTER_REPLAN_USER.format(
        **_book_prompt(book),
        volume_title=_esc(volume.title),
        volume_summary=_esc(volume.summary),
        completed_summaries=_esc(completed_summaries),
        reason=_esc(reason),
        pending_count=len(targets),
    )
    # Release the read transaction during the model call. The apply step
    # obtains a write lock before reading the current chapter slots again.
    db.rollback()
    full = ""
    async with aclosing(_stream(prompts.CHAPTER_REPLAN_SYSTEM, user)) as stream:
        async for ev, data in stream:
            if ev == "full":
                full = data
            else:
                yield ev, data

    try:
        sections = stages.parse_sections(
            full,
            level=2,
            minimum=len(targets),
            maximum=len(targets),
            label="章节细纲",
        )
        chapter_plans.stage_replanned_outlines(
            db, book_id, volume_id, targets, sections
        )
    except stages.InvalidStage as exc:
        db.rollback()
        yield ("error", _retry_message(exc))
        return
    stages.commit(db, None)

    yield ("log", f"已重新规划《{volume_title}》{len(targets)} 章细纲")
    yield ("done", "")
