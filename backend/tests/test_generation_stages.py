"""生成结构的整批校验、事务失败与旧版任务恢复回归测试。"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.agents import pipeline, prompts, stages
from app.db_base import Base
from app.memory import sync as memory_sync
from app.tasks import book_generation, novel_generation

WORLD = "## 规则\n规则正文\n## 地理\n地理正文\n## 冲突\n冲突正文\n## 组织\n组织正文"
CARD_FIELDS = "\n".join(f"- {name}：人物卡内容" for name in stages.CHARACTER_FIELDS)
CAST = f"# 林秋\n{CARD_FIELDS}\n# 沈雁\n{CARD_FIELDS}\n# 高谦\n{CARD_FIELDS}"
THREADS = "# 剧情线程\n- 身世 | 追查失散家人\n- 暗号 | 解开密文"
OUTLINE = f"## 第一卷 相遇\n结识盟友\n## 第二卷 告别\n解开密文\n{THREADS}"
CHAPTERS = "## 第1章 初识\n误入庭院\n## 第2章 相约\n解开密文"
PLAN = {
    "volume_count": 2,
    "chapters_per_volume": 2,
    "words_per_chapter": 2000,
    "character_count": 3,
}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/stages.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


class FakeStream:
    def __init__(self, replies: dict[str, list[str]]) -> None:
        super().__init__()
        self.replies = replies
        self.prompts: list[str] = []

    async def chat_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        self.prompts.append(prompt)
        yield self.replies[system].pop(0)


class WaitingStream(FakeStream):
    def __init__(self, system: str, output: str) -> None:
        super().__init__({system: [output]})
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        self.entered.set()
        await self.release.wait()
        async for chunk in super().chat_stream(prompt, system):
            yield chunk


def seed(db: Session) -> tuple[models.Book, models.GenerationTask]:
    book = models.Book(title="验证之书", target_words=8000)
    db.add(book)
    db.flush()
    task = models.GenerationTask(book_id=book.id, type="novel", status="running")
    db.add(task)
    book_generation.merge_checkpoint(task, **PLAN)
    db.commit()
    return book, task


async def skip_index(
    _db: Session, _book_id: str, _source_type: str, _source_id: str
) -> bool:
    return True


def events_for(
    db: Session,
    book_id: str,
    volume_id: str,
    stage: str,
    recovery: stages.Recovery | None,
) -> AsyncIterator[pipeline.Event]:
    if stage == "worldbuilding":
        return pipeline.gen_worldbuilding(db, book_id, recovery=recovery)
    if stage == "characters":
        return pipeline.gen_characters(db, book_id, 3, recovery=recovery)
    if stage == "outline":
        return pipeline.gen_outline(db, book_id, 2, recovery=recovery)
    return pipeline.gen_chapter_outlines(db, volume_id, 2, recovery=recovery)


@pytest.mark.parametrize("stage", ["worldbuilding", "characters", "outline"])
@pytest.mark.parametrize("change", ["add", "edit", "delete", "manual_add"])
async def test_author_structure_changes_during_stream_reject_the_generated_batch(
    db: Session, monkeypatch: pytest.MonkeyPatch, stage: str, change: str
) -> None:
    book, task = seed(db)
    book_id, task_id = book.id, task.id
    if stage == "worldbuilding":
        initial = models.Worldbuilding(
            book_id=book_id, title="规则", md_content="作者原文"
        )
        added = models.Worldbuilding(
            book_id=book_id, title="地理", md_content="作者新卡"
        )
        system, full, start = prompts.WORLDBUILDING_SYSTEM, WORLD, "## 地理"
        table, title_field, body_field = "worldbuilding", "title", "md_content"
    elif stage == "characters":
        initial = models.Character(book_id=book_id, name="林秋", md_content="作者原文")
        added = models.Character(book_id=book_id, name="沈雁", md_content="作者新卡")
        system, full, start = prompts.CHARACTERS_SYSTEM, CAST, "# 沈雁"
        table, title_field, body_field = "characters", "name", "md_content"
    else:
        initial = models.Volume(
            book_id=book_id, title="第一卷 相遇", summary="作者原文"
        )
        added = models.Volume(book_id=book_id, title="第二卷 告别", summary="作者新卡")
        system, full, start = prompts.OUTLINE_SYSTEM, OUTLINE, "## 第二卷"
        table, title_field, body_field = "volumes", "title", "summary"
    manual = change == "manual_add"
    if manual:
        setattr(initial, title_field, "已有的作者标题")
    initial_title, added_title = (
        getattr(initial, title_field),
        getattr(added, title_field),
    )
    db.add(initial)
    db.flush()
    initial_id = initial.id
    checkpoint = task.checkpoint_json
    db.commit()

    # Manual generation still requests a full batch. Recovery requests only missing rows.
    llm = WaitingStream(system, full if manual else full[full.index(start) :])
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)
    monkeypatch.setattr(pipeline.memory_sync, "process_source", skip_index)

    def record(counts: dict[str, int]) -> None:
        book_generation.merge_checkpoint(task, counts=counts)

    async def generate() -> list[pipeline.Event]:
        recovery = None if manual else stages.Recovery({"threads": 2}, record)
        return [event async for event in events_for(db, book_id, "", stage, recovery)]

    running = asyncio.create_task(generate())
    try:
        await asyncio.wait_for(llm.entered.wait(), timeout=3)
        assert not db.in_transaction()
        with Session(db.get_bind()) as author:
            if change in {"add", "manual_add"}:
                author.add(added)
                expected = {(initial_title, "作者原文"), (added_title, "作者新卡")}
            else:
                saved = author.get(type(initial), initial_id)
                assert saved is not None
                if change == "edit":
                    setattr(saved, body_field, "作者更新后的原文")
                    expected = {(initial_title, "作者更新后的原文")}
                else:
                    author.delete(saved)
                    expected = set()
            author.commit()
    finally:
        llm.release.set()
    events = await asyncio.wait_for(running, timeout=3)
    assert events[-1][0] == "error"
    assert ("重名" if manual else "已变动") in events[-1][1]
    assert all(event != "done" for event, _ in events)
    assert not db.in_transaction()

    with Session(db.get_bind()) as check:
        rows = check.execute(text(f"SELECT {title_field}, {body_field} FROM {table}"))
        assert set(rows) == expected
        saved_task = check.get(models.GenerationTask, task_id)
        assert saved_task is not None and saved_task.checkpoint_json == checkpoint
        assert check.scalar(select(func.count()).select_from(models.MemorySyncJob)) == 0
        assert check.scalar(select(func.count()).select_from(models.PlotThread)) == 0


@pytest.mark.parametrize("stage", ["worldbuilding", "characters", "outline"])
async def test_recovery_rechecks_complete_structure_before_recording_skip(
    db: Session, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    book, _ = seed(db)
    book_id = book.id
    if stage == "worldbuilding":
        table, body_field = "worldbuilding", "md_content"
        rows = [
            models.Worldbuilding(book_id=book_id, title=f"设定{i}", md_content="原文")
            for i in range(4)
        ]
    elif stage == "characters":
        table, body_field = "characters", "md_content"
        rows = [
            models.Character(book_id=book_id, name=f"人物{i}", md_content="原文")
            for i in range(3)
        ]
    else:
        table, body_field = "plot_threads", "description"
        rows = [models.PlotThread(book_id=book_id, name="剧情", description="原文")]
        db.add_all(
            models.Volume(book_id=book_id, title=f"第{i}卷", summary="原文")
            for i in (1, 2)
        )
    db.add_all(rows)
    db.commit()
    monkeypatch.setattr(
        pipeline, "get_llm", lambda: pytest.fail("完整恢复无需请求模型")
    )

    changed = False
    bind = db.get_bind()

    def edit_before_lock(
        _conn: object, _cursor: object, stmt: str, *_args: object
    ) -> None:
        nonlocal changed
        if changed or stmt != "BEGIN IMMEDIATE":
            return
        changed = True
        with Session(bind) as author:
            author.execute(text(f"UPDATE {table} SET {body_field}='作者并发修改'"))
            author.commit()

    event.listen(bind, "before_cursor_execute", edit_before_lock)
    try:
        recovery = stages.Recovery(record=lambda _: pytest.fail("过期快照不能记录跳过"))
        events = [event async for event in events_for(db, book_id, "", stage, recovery)]
    finally:
        event.remove(bind, "before_cursor_execute", edit_before_lock)
    assert changed and events[-1][0] == "error" and "已变动" in events[-1][1]
    assert not db.in_transaction()


async def test_manual_character_generation_still_appends_distinct_names(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, _ = seed(db)
    existing = models.Character(
        book_id=book.id, name="已有作者人物", md_content="作者原文"
    )
    db.add(existing)
    db.commit()
    llm = FakeStream({prompts.CHARACTERS_SYSTEM: [CAST]})
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)

    events = [event async for event in pipeline.gen_characters(db, book.id, 3)]
    assert events[-1] == ("done", "")
    assert set(db.scalars(select(models.Character.name))) == {
        "已有作者人物",
        "林秋",
        "沈雁",
        "高谦",
    }
    assert existing.md_content == "作者原文"


@pytest.mark.parametrize(
    ("stage", "system", "reply", "table", "rows"),
    [
        ("worldbuilding", prompts.WORLDBUILDING_SYSTEM, WORLD, "worldbuilding", 4),
        ("characters", prompts.CHARACTERS_SYSTEM, CAST, "characters", 3),
        ("outline", prompts.OUTLINE_SYSTEM, OUTLINE, "plot_threads", 2),
        ("chapter_outlines", prompts.CHAPTER_OUTLINE_SYSTEM, CHAPTERS, "chapters", 2),
    ],
)
async def test_stage_sql_and_checkpoint_roll_back_together(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    system: str,
    reply: str,
    table: str,
    rows: int,
) -> None:
    book, task = seed(db)
    volume = models.Volume(book_id=book.id, title="先存卷", summary="卷纲")
    if stage == "chapter_outlines":
        db.add(volume)
        db.commit()
    volume_id = volume.id if stage == "chapter_outlines" else ""
    llm = FakeStream({system: [reply, reply]})
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)
    monkeypatch.setattr(pipeline.memory_sync, "process_source", skip_index)
    # 故障发生在整批内容与 outbox 已 flush 后的检查点更新，模拟提交前中断。
    db.execute(
        text("""
        CREATE TRIGGER fail_stage_commit BEFORE UPDATE OF checkpoint_json ON generation_tasks
        WHEN NEW.checkpoint_json LIKE '%generation_stages%'
        BEGIN SELECT RAISE(ABORT, 'injected commit failure'); END
    """)
    )
    db.commit()

    def generate(recovery: stages.Recovery) -> AsyncIterator[pipeline.Event]:
        return events_for(db, book.id, volume_id, stage, recovery)

    with pytest.raises(IntegrityError, match="injected commit failure"):
        await novel_generation._run_stage(
            db, task, stage, generate, volume_id=volume_id or None
        )

    with Session(db.get_bind()) as restarted:
        saved = restarted.get(models.GenerationTask, task.id)
        assert saved is not None
        assert "generation_stages" not in book_generation.load_checkpoint(saved)
        assert restarted.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        if stage == "outline":
            assert (
                restarted.scalar(select(func.count()).select_from(models.Volume)) == 0
            )
        if stage == "worldbuilding":
            assert (
                restarted.scalar(select(func.count()).select_from(models.MemorySyncJob))
                == 0
            )

    db.execute(text("DROP TRIGGER fail_stage_commit"))
    db.commit()
    await novel_generation._run_stage(
        db, task, stage, generate, volume_id=volume_id or None
    )
    assert db.scalar(text(f"SELECT count(*) FROM {table}")) == rows
    assert "generation_stages" in book_generation.load_checkpoint(task)
    await novel_generation._run_stage(
        db, task, stage, generate, volume_id=volume_id or None
    )
    assert db.scalar(text(f"SELECT count(*) FROM {table}")) == rows
    assert len(llm.prompts) == 2


@pytest.mark.parametrize(
    ("stage", "system", "reply", "error"),
    [
        ("worldbuilding", prompts.WORLDBUILDING_SYSTEM, "未分节的文本", "格式"),
        ("worldbuilding", prompts.WORLDBUILDING_SYSTEM, "## 世界\n片段", "4 到 6"),
        (
            "characters",
            prompts.CHARACTERS_SYSTEM,
            f"# 林秋\n{CARD_FIELDS}",
            "需要 3 项",
        ),
        (
            "characters",
            prompts.CHARACTERS_SYSTEM,
            CAST.replace("- 外貌：人物卡内容", "- 外貌："),
            "外貌",
        ),
        ("outline", prompts.OUTLINE_SYSTEM, OUTLINE.split("# 剧情线程")[0], "缺少"),
        (
            "outline",
            prompts.OUTLINE_SYSTEM,
            OUTLINE.replace("第二卷", "第三卷"),
            "编号",
        ),
        (
            "outline",
            prompts.OUTLINE_SYSTEM,
            OUTLINE.replace("- 身世 | 追查失散家人", "- 身世"),
            "逐行",
        ),
        (
            "chapter_outlines",
            prompts.CHAPTER_OUTLINE_SYSTEM,
            CHAPTERS.split("## 第2章")[0],
            "需要 2 项",
        ),
        (
            "chapter_outlines",
            prompts.CHAPTER_OUTLINE_SYSTEM,
            CHAPTERS + "\n## 第3章 多余\n正文",
            "返回了 3 项",
        ),
        (
            "chapter_outlines",
            prompts.CHAPTER_OUTLINE_SYSTEM,
            CHAPTERS.replace("第2章", "第9章"),
            "编号",
        ),
        (
            "chapter_outlines",
            prompts.CHAPTER_OUTLINE_SYSTEM,
            CHAPTERS.replace("解开密文", ""),
            "缺少有效标题或内容",
        ),
    ],
)
async def test_invalid_model_stage_has_no_partial_writes(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    system: str,
    reply: str,
    error: str,
) -> None:
    book, _ = seed(db)
    volume = models.Volume(book_id=book.id, title="旧卷", summary="旧卷纲")
    chapter = models.Chapter(
        book_id=book.id, volume=volume, title="旧章", status="done"
    )
    chapter.content = models.ChapterContent(body="已有人工作品")
    db.add(chapter)
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", lambda: FakeStream({system: [reply]}))
    events = [event async for event in events_for(db, book.id, volume.id, stage, None)]
    assert events[-1][0] == "error"
    assert error in events[-1][1] and "重试" in events[-1][1]
    assert all(event != "done" for event, _ in events)
    assert db.scalar(select(func.count()).select_from(models.Worldbuilding)) == 0
    assert db.scalar(select(func.count()).select_from(models.Character)) == 0
    assert db.scalar(select(func.count()).select_from(models.Volume)) == 1
    assert db.scalar(select(func.count()).select_from(models.PlotThread)) == 0
    assert db.scalar(select(func.count()).select_from(models.Chapter)) == 1
    content = chapter.content
    assert content is not None and content.body == "已有人工作品"


async def test_resume_fills_legacy_stages_without_overwriting_saved_work(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book, task = seed(db)
    world = models.Worldbuilding(
        book_id=book.id, title="旧规则", md_content="人工修改的规则"
    )
    character = models.Character(
        book_id=book.id,
        name="旧角色",
        md_content="人工修改的人物卡",
        state_json='{"alive":true}',
    )
    first = models.Volume(
        book_id=book.id,
        title="第一卷 人工标题",
        order_index=0,
        summary="人工修改的卷纲",
    )
    second = models.Volume(
        book_id=book.id, title="第二卷 人工标题", order_index=1, summary="保留的卷纲"
    )
    chapter = models.Chapter(
        book_id=book.id,
        volume=first,
        title="第1章 人工标题",
        outline="人工修改的细纲",
        status="done",
    )
    chapter.content = models.ChapterContent(body="保留原有正文")
    db.add_all([world, character, first, second, chapter])
    db.commit()

    llm = FakeStream(
        {
            prompts.WORLDBUILDING_SYSTEM: [WORLD[WORLD.index("## 地理") :]],
            prompts.CHARACTERS_SYSTEM: [CAST[CAST.index("# 沈雁") :]],
            prompts.OUTLINE_SYSTEM: [THREADS],
            prompts.CHAPTER_OUTLINE_SYSTEM: ["## 第2章 新章\n衔接旧章", CHAPTERS],
            prompts.CHAPTER_BODY_SYSTEM: ["模型补写正文"] * 3,
        }
    )
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)
    monkeypatch.setattr(pipeline.memory_sync, "process_source", skip_index)

    async def context(_db: Session, _chapter: models.Chapter) -> str:
        return ""

    async def chapter_memory(_db: Session, _chapter: models.Chapter) -> bool:
        return True

    monkeypatch.setattr(pipeline.memory, "build_chapter_context", context)
    monkeypatch.setattr(pipeline.memory_sync, "process_chapter_memory", chapter_memory)
    await novel_generation.run_novel_task(db, task)

    assert (task.status, task.progress, task.error) == ("done", 100, "")
    assert world.md_content == "人工修改的规则"
    assert (character.md_content, character.state_json) == (
        "人工修改的人物卡",
        '{"alive":true}',
    )
    assert (first.title, first.summary, second.summary) == (
        "第一卷 人工标题",
        "人工修改的卷纲",
        "保留的卷纲",
    )
    assert chapter.outline == "人工修改的细纲"
    content = chapter.content
    assert content is not None and content.body == "保留原有正文"
    assert db.scalar(select(func.count()).select_from(models.Worldbuilding)) == 4
    assert db.scalar(select(func.count()).select_from(models.Character)) == 3
    assert db.scalar(select(func.count()).select_from(models.Volume)) == 2
    assert db.scalar(select(func.count()).select_from(models.PlotThread)) == 2
    assert db.scalar(select(func.count()).select_from(models.Chapter)) == 4
    assert llm.replies == {system: [] for system in llm.replies}
    # 再次进入同一任务时，完成阶段和既有正文均无需调用模型。
    task.status = "running"
    db.commit()
    await novel_generation.run_novel_task(db, task)
    assert task.status == "done"
    assert len(llm.prompts) == 8


@pytest.mark.parametrize("has_earlier_chapter", [False, True])
async def test_resuming_earlier_volume_inserts_without_reordering_existing_prose(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    has_earlier_chapter: bool,
) -> None:
    book, task = seed(db)
    earlier = models.Volume(
        book_id=book.id, title="第一卷", order_index=0, summary="早期卷纲"
    )
    later = models.Volume(
        book_id=book.id, title="第二卷", order_index=1, summary="后续卷纲"
    )
    earlier_count = int(has_earlier_chapter)
    kept = [
        models.Chapter(
            book_id=book.id,
            volume=later,
            title=f"后续手工章{index}",
            outline=f"手工细纲{index}",
            order_index=index + earlier_count,
            status="done",
        )
        for index in range(2)
    ]
    for chapter in kept:
        chapter.content = models.ChapterContent(body=f"{chapter.title}正文")
    db.add_all([earlier, later, *kept])
    if has_earlier_chapter:
        db.add(
            models.Chapter(
                book_id=book.id,
                volume=earlier,
                title="第1章 既有",
                order_index=0,
                outline="前卷旧细纲",
            )
        )
    db.flush()
    for chapter in kept:
        memory_sync.enqueue_chapter(db, chapter)
    db.commit()
    later_ids = [chapter.id for chapter in kept]
    original_hashes = [memory_sync.chapter_hash(chapter) for chapter in kept]
    original_revisions = {
        job.id: job.revision for job in db.scalars(select(models.MemorySyncJob))
    }
    reply = CHAPTERS[CHAPTERS.index("## 第2章") :] if has_earlier_chapter else CHAPTERS
    llm = FakeStream({prompts.CHAPTER_OUTLINE_SYSTEM: [reply, reply]})
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)

    # Refusing the checkpoint must roll back the new chapters and shifted
    # indices, and leave already-enqueued memory revisions intact.
    db.execute(
        text("""
        CREATE TRIGGER fail_shift_commit BEFORE UPDATE OF checkpoint_json ON generation_tasks
        WHEN NEW.checkpoint_json LIKE '%generation_stages%'
        BEGIN SELECT RAISE(ABORT, 'injected shift failure'); END
    """)
    )
    db.commit()

    def generate(recovery: stages.Recovery) -> AsyncIterator[pipeline.Event]:
        return pipeline.gen_chapter_outlines(db, earlier.id, 2, recovery=recovery)

    with pytest.raises(IntegrityError, match="injected shift failure"):
        await novel_generation._run_stage(
            db,
            task,
            "chapter_outlines",
            generate,
            volume_id=earlier.id,
        )
    assert [chapter.order_index for chapter in kept] == list(
        range(earlier_count, earlier_count + 2)
    )
    assert (
        db.scalar(select(func.count()).select_from(models.Chapter)) == earlier_count + 2
    )
    assert {
        job.id: job.revision for job in db.scalars(select(models.MemorySyncJob))
    } == original_revisions
    assert "generation_stages" not in book_generation.load_checkpoint(task)

    db.execute(text("DROP TRIGGER fail_shift_commit"))
    db.commit()
    await novel_generation._run_stage(
        db,
        task,
        "chapter_outlines",
        generate,
        volume_id=earlier.id,
    )
    chapters = list(
        db.scalars(select(models.Chapter).order_by(models.Chapter.order_index))
    )
    assert [chapter.order_index for chapter in chapters] == [0, 1, 2, 3]
    assert [chapter.volume_id for chapter in chapters[:2]] == [earlier.id] * 2
    assert [chapter.id for chapter in chapters[2:]] == later_ids
    for index, chapter in enumerate(chapters[2:]):
        assert (chapter.title, chapter.outline) == (
            f"后续手工章{index}",
            f"手工细纲{index}",
        )
        content = chapter.content
        assert content is not None and content.body == f"后续手工章{index}正文"
        job = memory_sync.find_job(db, book.id, memory_sync.CHAPTER, chapter.id)
        assert job is not None and job.source_hash == memory_sync.chapter_hash(chapter)
        assert job.source_hash == original_hashes[index]
    assert {
        job.id: job.revision for job in db.scalars(select(models.MemorySyncJob))
    } == original_revisions


@pytest.mark.parametrize(("later_order", "expected_later"), [(8, 8), (6, 7)])
async def test_recovered_outlines_use_available_gap_and_keep_loose_chapter_order(
    db: Session, monkeypatch: pytest.MonkeyPatch, later_order: int, expected_later: int
) -> None:
    book, _ = seed(db)
    earlier = models.Volume(book_id=book.id, title="第一卷", order_index=0)
    later = models.Volume(book_id=book.id, title="第二卷", order_index=1)
    retained = models.Chapter(
        book_id=book.id,
        volume=earlier,
        title="第1章 初识",
        outline="旧纲",
        order_index=2,
    )
    loose_before = models.Chapter(book_id=book.id, title="未归卷前", order_index=4)
    following = models.Chapter(
        book_id=book.id, volume=later, title="后卷一章", order_index=later_order
    )
    loose_after = models.Chapter(book_id=book.id, title="未归卷后", order_index=9)
    db.add_all([retained, loose_before, following, loose_after])
    db.commit()
    monkeypatch.setattr(
        pipeline,
        "get_llm",
        lambda: FakeStream(
            {
                prompts.CHAPTER_OUTLINE_SYSTEM: [
                    "## 第2章 相约\n续写一段\n## 第3章 远行\n启程"
                ]
            }
        ),
    )

    events = [
        event
        async for event in pipeline.gen_chapter_outlines(
            db, earlier.id, 3, recovery=stages.Recovery()
        )
    ]

    assert events[-1] == ("done", "")
    chapters = list(
        db.scalars(
            select(models.Chapter)
            .where(models.Chapter.book_id == book.id)
            .order_by(models.Chapter.order_index)
        )
    )
    assert [chapter.title for chapter in chapters] == [
        "第1章 初识",
        "未归卷前",
        "第2章 相约",
        "第3章 远行",
        "后卷一章",
        "未归卷后",
    ]
    assert [chapter.order_index for chapter in chapters] == [
        2,
        4,
        5,
        6,
        expected_later,
        9 + expected_later - later_order,
    ]
    assert [chapter.volume_id for chapter in chapters] == [
        earlier.id,
        None,
        earlier.id,
        earlier.id,
        later.id,
        None,
    ]
