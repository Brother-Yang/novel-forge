"""真实 SQLite 与脚本化 LLM 验证生成完成边界,禁止访问实际模型/记忆库。"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models, schemas
from app.agents import orchestrator, pipeline, planner, prompts, tools
from app.agents.completion import chapter_counts, chapter_is_complete
from app.api import chapters as chapter_api
from app.config import settings
from app.db_base import Base
from app.llm.base import ToolCall, ToolChatResult
from app.tasks import agent_generation, book_generation, novel_generation


class ScriptedLLM:
    def __init__(
        self, body: str = "有效章节正文", decisions: list[ToolChatResult] | None = None
    ) -> None:
        super().__init__()
        self.body = body
        self.decisions = list(decisions or [])
        self.calls = 0
        self.body_prompts: list[str] = []

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        if system == prompts.CHAPTER_BODY_SYSTEM:
            self.body_prompts.append(prompt)
        yield self.body

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        return "章节摘要"

    async def embed(self, text: str) -> list[float]:
        raise AssertionError("不应调用向量服务")

    async def chat_with_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ToolChatResult:
        self.calls += 1
        assert self.decisions, "决策脚本已耗尽"
        return self.decisions.pop(0)


def call(name: str, **arguments: object) -> ToolChatResult:
    return ToolChatResult("", [ToolCall("test-call", name, arguments)])


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/completion.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    monkeypatch.setattr(settings, "agent_max_steps", 3)

    async def context(db: Session, chapter: models.Chapter) -> str:
        return "测试上下文"

    async def skip_memory(db: Session, chapter: models.Chapter) -> bool:
        return True

    monkeypatch.setattr(pipeline.memory, "build_chapter_context", context)
    monkeypatch.setattr(pipeline.memory_sync, "process_chapter_memory", skip_memory)


def seed(
    db: Session, *, body: str | None = None, status: str = "outline"
) -> tuple[models.Book, models.Chapter]:
    book = models.Book(title="测试小说", brief="故事想法")
    chapter = models.Chapter(book=book, title="第一章", status=status)
    if body is not None:
        chapter.content = models.ChapterContent(body=body)
        chapter.word_count = len(body)
    db.add(book)
    db.commit()
    return book, chapter


def task_for(
    db: Session, book: models.Book, type_: str = "agent"
) -> models.GenerationTask:
    task = models.GenerationTask(book_id=book.id, type=type_, status="running")
    db.add(task)
    db.commit()
    return task


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> None:
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)


async def test_only_last_id_gets_ending_instruction_when_chapter_order_ties(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = models.Book(title="并列末章")
    last = models.Chapter(id="z-last", book=book, title="后章", order_index=3)
    prior = models.Chapter(id="a-prior", book=book, title="前章", order_index=3)
    db.add(book)
    db.commit()
    llm = ScriptedLLM()
    use_llm(monkeypatch, llm)

    for chapter in (prior, last):
        events = [event async for event in pipeline.gen_chapter_body(db, chapter.id)]
        assert events[-1] == ("done", "")
    assert len(llm.body_prompts) == 2
    assert prompts.CHAPTER_BODY_ENDING_NOTE not in llm.body_prompts[0]
    assert prompts.CHAPTER_BODY_ENDING_NOTE in llm.body_prompts[1]


def test_progress_counts_nonblank_body_with_python_unicode_whitespace(
    db: Session,
) -> None:
    whitespace = "".join(chr(code) for code in range(0x110000) if chr(code).isspace())
    book, _ = seed(db, body=whitespace, status="done")
    complete = models.Chapter(book=book, title="完成", status="done")
    complete.content = models.ChapterContent(body=f"{whitespace}正文{whitespace}")
    pending = models.Chapter(book=book, title="未完成", status="outline")
    pending.content = models.ChapterContent(body="未完成正文")
    db.add_all(
        [complete, pending, models.Chapter(book=book, title="缺正文", status="done")]
    )
    db.commit()
    book_id = book.id
    task = task_for(db, book)
    task_id = task.id
    db.expunge_all()

    assert chapter_counts(db, book_id) == (4, 1)
    saved_task = db.get(models.GenerationTask, task_id)
    assert saved_task is not None
    agent_generation._update_progress(db, saved_task)
    assert saved_task.progress == 27
    assert not any(
        isinstance(item, models.ChapterContent) for item in db.identity_map.values()
    )


async def test_decision_budget_exhaustion_persists_error_and_can_retry(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, chapter = seed(db)
    task = task_for(db, book)
    llm = ScriptedLLM(decisions=[call("get_book_state") for _ in range(3)])
    use_llm(monkeypatch, llm)

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert llm.calls == 3
    assert task.status == "error"
    assert task.progress < 100
    assert "最大决策步数" in task.error
    launched: list[str] = []
    monkeypatch.setattr(book_generation, "launch", launched.append)
    book_generation.resume_task(db, task.id)
    assert launched == [task.id]
    llm.decisions = [call("write_chapter", chapter_id=chapter.id), call("finish_book")]
    await agent_generation.run_agent_task(db, task)
    db.refresh(task)
    assert (task.status, task.progress, task.error) == ("done", 100, "")
    db.refresh(chapter)
    assert chapter_is_complete(chapter)


@pytest.mark.parametrize("output", ["", " \n\t\u3000"])
@pytest.mark.parametrize("old_body", [None, "已有正文应当保留"])
async def test_blank_generation_preserves_existing_body_and_completion(
    db: Session, monkeypatch: pytest.MonkeyPatch, output: str, old_body: str | None
) -> None:
    book, chapter = seed(db, body=old_body, status="done" if old_body else "outline")
    original_count = chapter.word_count
    use_llm(monkeypatch, ScriptedLLM(body=output))

    events = [event async for event in pipeline.gen_chapter_body(db, chapter.id)]

    db.refresh(chapter)
    assert any(ev == "error" for ev, _ in events)
    assert not any(ev == "done" for ev, _ in events)
    assert chapter.status == ("done" if old_body else "outline")
    assert chapter.word_count == original_count
    assert (chapter.content.body if chapter.content else None) == old_body
    assert book_generation._pending_chapters(db, book.id) == (
        [] if old_body else [chapter.id]
    )
    use_llm(monkeypatch, ScriptedLLM(body="重试后的有效正文"))
    retried = [event async for event in pipeline.gen_chapter_body(db, chapter.id)]
    assert retried[-1] == ("done", "")
    db.refresh(chapter)
    assert chapter_is_complete(chapter)
    assert chapter.content is not None
    assert chapter.content.body == "重试后的有效正文"


@pytest.mark.parametrize("interruption", ["model_error", "cancel"])
async def test_interrupted_rewrite_does_not_make_background_task_regenerate_chapter(
    db: Session, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    original_body = "已经完成的正文"
    book, chapter = seed(db, body=original_body, status="done")
    chapter_id = chapter.id
    entered, release = asyncio.Event(), asyncio.Event()
    model_calls = 0

    class InterruptedModel:
        async def chat_stream(
            self, _prompt: str, system: str = ""
        ) -> AsyncIterator[str]:
            nonlocal model_calls
            model_calls += 1
            yield "尚未生成完的新正文"
            entered.set()
            await release.wait()
            raise RuntimeError("测试模型失败")

    async def collect() -> list[pipeline.Event]:
        return [event async for event in pipeline.gen_chapter_body(db, chapter_id)]

    monkeypatch.setattr(pipeline, "get_llm", InterruptedModel)
    rewrite = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with Session(db.get_bind()) as reader:
            active = reader.get(models.Chapter, chapter_id)
            assert active is not None and chapter_is_complete(active)
        if interruption == "cancel":
            rewrite.cancel()
            with pytest.raises(asyncio.CancelledError):
                await rewrite
        else:
            release.set()
            with pytest.raises(RuntimeError, match="测试模型失败"):
                await asyncio.wait_for(rewrite, 2)
    finally:
        if not rewrite.done():
            rewrite.cancel()
            await asyncio.gather(rewrite, return_exceptions=True)

    db.refresh(chapter)
    assert chapter_is_complete(chapter)
    assert chapter.word_count == len(original_body)
    assert chapter.content is not None and chapter.content.body == original_body
    assert not list(db.scalars(select(models.MemorySyncJob)))

    task = task_for(db, book, "book")
    await book_generation.run_chapter_bodies(db, task)
    assert (task.status, task.progress) == ("done", 100)
    assert model_calls == 1


@pytest.mark.parametrize("output", ["模型生成的正文", " \n\t\u3000"])
@pytest.mark.parametrize("change", ["save", "delete", "outline", "volume"])
async def test_chapter_generation_preserves_concurrent_author_changes(
    db: Session, monkeypatch: pytest.MonkeyPatch, output: str, change: str
) -> None:
    book, chapter = seed(db, body="原有正文", status="done")
    original_volume = models.Volume(book_id=book.id, title="第一卷", order_index=0)
    new_volume = models.Volume(book_id=book.id, title="第二卷", order_index=1)
    chapter.volume = original_volume
    chapter.outline = "原有章纲"
    db.add(new_volume)
    db.commit()
    chapter_id, new_volume_id = chapter.id, new_volume.id
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedModel:
        async def chat_stream(
            self, _prompt: str, system: str = ""
        ) -> AsyncIterator[str]:
            entered.set()
            await release.wait()
            yield output

    async def collect() -> list[pipeline.Event]:
        return [event async for event in pipeline.gen_chapter_body(db, chapter_id)]

    async def unexpected_memory(_db: Session, _chapter: models.Chapter) -> bool:
        raise AssertionError("冲突后不应处理生成正文的记忆")

    def saved_fields(row: models.Chapter | None) -> object:
        if row is None:
            return None
        return (
            row.title,
            row.outline,
            row.status,
            row.volume_id,
            row.order_index,
            row.word_count,
            row.content.body if row.content else None,
        )

    def memory_jobs(session: Session) -> set[tuple[str, str, str, str]]:
        return {
            (job.id, job.revision, job.status, job.source_hash)
            for job in session.scalars(select(models.MemorySyncJob))
        }

    monkeypatch.setattr(pipeline, "get_llm", DelayedModel)
    monkeypatch.setattr(
        pipeline.memory_sync, "process_chapter_memory", unexpected_memory
    )
    generation = asyncio.create_task(collect())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with Session(db.get_bind()) as editor:
            active = editor.get(models.Chapter, chapter_id)
            assert active is not None and chapter_is_complete(active)
            if change == "save":
                chapter_api.update_chapter_content(
                    chapter_id,
                    schemas.ChapterContentUpdate(body="作者另存的正文"),
                    editor,
                )
            elif change == "delete":
                chapter_api.delete_chapter(chapter_id, editor)
            elif change == "outline":
                chapter_api.update_chapter(
                    chapter_id,
                    schemas.ChapterUpdate(title="作者的新标题", outline="作者的新章纲"),
                    editor,
                )
            else:
                chapter_api.update_chapter(
                    chapter_id,
                    schemas.ChapterUpdate(volume_id=new_volume_id, order_index=10),
                    editor,
                )
            author_state = saved_fields(editor.get(models.Chapter, chapter_id))
            author_memory = memory_jobs(editor)
    finally:
        release.set()

    events = await asyncio.wait_for(generation, 2)
    assert events[-1][0] == "error"
    if change == "delete":
        assert "章节已删除" in events[-1][1]
    else:
        assert "章节已修改" in events[-1][1]
    with Session(db.get_bind()) as reader:
        assert saved_fields(reader.get(models.Chapter, chapter_id)) == author_state
        assert memory_jobs(reader) == author_memory
        if change == "delete":
            assert reader.get(models.ChapterContent, chapter_id) is None


@pytest.mark.parametrize(
    ("status", "body"),
    [("done", None), ("done", ""), ("done", " \n\t"), ("outline", "正文")],
)
async def test_finish_and_runtime_reject_incomplete_chapter(
    db: Session, monkeypatch: pytest.MonkeyPatch, status: str, body: str | None
) -> None:
    book, _ = seed(db, body=body, status=status)
    task = task_for(db, book)
    with pytest.raises(tools.ToolError, match="未完成或正文为空"):
        await tools.tool_finish_book(db, book.id, {})

    async def premature_done(
        db: Session, book_id: str
    ) -> AsyncIterator[tuple[str, str]]:
        yield "done", ""

    monkeypatch.setattr(orchestrator, "run_agent", premature_done)
    await agent_generation.run_agent_task(db, task)
    db.refresh(task)
    assert task.status == "error"
    assert task.progress < 100


async def test_finish_rejects_no_chapters_or_partially_written_book(
    db: Session,
) -> None:
    book = models.Book(title="空书")
    db.add(book)
    db.commit()
    with pytest.raises(tools.ToolError, match="尚无"):
        await tools.tool_finish_book(db, book.id, {})
    chapter = models.Chapter(book=book, title="已完成", status="done")
    chapter.content = models.ChapterContent(body="完成正文")
    db.add(chapter)
    db.add(models.Chapter(book=book, title="未完成", status="outline"))
    db.commit()
    with pytest.raises(tools.ToolError, match="仍有 1 章"):
        await tools.tool_finish_book(db, book.id, {})


async def test_early_finish_failure_is_observed_then_generation_can_complete(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, chapter = seed(db)
    task = task_for(db, book)
    llm = ScriptedLLM(
        decisions=[
            call("finish_book", reason="提前结束"),
            call("write_chapter", chapter_id=chapter.id),
            call("finish_book", reason="正文完成"),
        ]
    )
    use_llm(monkeypatch, llm)

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert task.status == "done"
    assert task.progress == 100
    traces = db.query(models.AgentTraceEntry).filter_by(task_id=task.id).all()
    assert any(
        '"ok": false' in trace.payload and "未完成或正文为空" in trace.payload
        for trace in traces
    )
    assert llm.calls == 3


async def test_repeated_early_finish_is_error_not_done(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, _ = seed(db)
    task = task_for(db, book)
    use_llm(monkeypatch, ScriptedLLM(decisions=[call("finish_book") for _ in range(3)]))

    await agent_generation.run_agent_task(db, task)

    db.refresh(task)
    assert task.status == "error"
    assert task.progress < 100


@pytest.mark.parametrize("emit_done", [False, True])
async def test_runtime_never_completes_book_without_chapters(
    db: Session, monkeypatch: pytest.MonkeyPatch, emit_done: bool
) -> None:
    book = models.Book(title="空书")
    db.add(book)
    db.commit()
    task = task_for(db, book)

    async def incomplete_run(
        db: Session, book_id: str
    ) -> AsyncIterator[tuple[str, str]]:
        if emit_done:
            yield "done", ""

    monkeypatch.setattr(orchestrator, "run_agent", incomplete_run)
    await agent_generation.run_agent_task(db, task)
    db.refresh(task)
    assert task.status == "error"
    assert task.progress < 100


@pytest.mark.parametrize("type_", ["book", "novel"])
async def test_batch_resume_regenerates_empty_done_and_preserves_completed_chapters(
    db: Session, monkeypatch: pytest.MonkeyPatch, type_: str
) -> None:
    book, chapter = seed(db, body=" \n\t", status="done")
    completed = models.Chapter(
        book=book, title="保留章节", status="done", order_index=1
    )
    completed.content = models.ChapterContent(body="原有有效正文")
    db.add(completed)
    volume = models.Volume(book=book, title="第一卷", summary="已有大纲")
    db.add(volume)
    db.flush()
    chapter.volume = volume
    chapter.outline = "第1章细纲"
    completed.volume = volume
    completed.outline = "第2章细纲"
    db.add_all(
        [
            models.Worldbuilding(
                book_id=book.id, title=f"设定{i}", md_content="已有设定"
            )
            for i in range(4)
        ]
    )
    db.add_all(
        [
            models.Character(book_id=book.id, name=f"人物{i}", md_content="已有人物卡")
            for i in range(3)
        ]
    )
    db.add(models.PlotThread(book_id=book.id, name="旧伏笔", description="已有描述"))
    db.commit()
    task = task_for(db, book, type_)
    llm = ScriptedLLM(body=" \n")
    use_llm(monkeypatch, llm)

    async def plan(db: Session, task: models.GenerationTask) -> planner.NovelPlan:
        return planner.NovelPlan(
            title=book.title,
            genre="",
            tone="",
            intro="",
            volume_count=1,
            chapters_per_volume=2,
            words_per_chapter=2000,
            character_count=3,
            key_elements=[],
        )

    monkeypatch.setattr(novel_generation, "_ensure_plan", plan)
    run = (
        book_generation.run_chapter_bodies
        if type_ == "book"
        else novel_generation.run_novel_task
    )
    await run(db, task)
    db.refresh(task)
    db.refresh(chapter)
    assert task.status == "error"
    assert task.progress < 100
    assert chapter.status == "outline"
    assert chapter.content is not None
    assert chapter.content.body == " \n\t"

    launched: list[str] = []
    monkeypatch.setattr(book_generation, "launch", launched.append)
    book_generation.resume_task(db, task.id)
    assert launched == [task.id]
    llm.body = "恢复成功的正文"
    await run(db, task)
    db.refresh(task)
    db.refresh(chapter)
    db.refresh(completed)
    assert (task.status, task.progress) == ("done", 100)
    assert chapter_is_complete(chapter)
    completed_content = completed.content
    assert completed_content is not None
    assert completed_content.body == "原有有效正文"


async def test_blank_done_chapter_can_be_repaired_without_review_approval(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, chapter = seed(db, body=" \n", status="done")
    use_llm(monkeypatch, ScriptedLLM())
    result = await tools.tool_write_chapter(db, book.id, {"chapter_id": chapter.id})
    assert result["ok"] is True
    db.refresh(chapter)
    assert chapter_is_complete(chapter)


async def test_completion_requires_chapters_in_every_planned_volume(
    db: Session,
) -> None:
    book, _ = seed(db, body="完成正文", status="done")
    db.add(models.Volume(book=book, title="尚未生成章节的卷"))
    db.commit()
    with pytest.raises(tools.ToolError, match="仍有卷未生成章节"):
        await tools.tool_finish_book(db, book.id, {})
