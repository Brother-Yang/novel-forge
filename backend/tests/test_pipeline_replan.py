"""app/agents/pipeline.py::replan_volume_chapters 测试:只重新规划该卷尚未
完成正文的章节细纲,已完成章节与分卷大纲本身保持不变。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models, schemas
from app.agents import pipeline
from app.api import chapters as chapter_api
from app.db_base import Base
from app.memory import sync as memory_sync


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/pipeline_replan_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class FakeStreamLLM:
    def __init__(self, reply: str) -> None:
        super().__init__()
        self._reply = reply
        self.last_prompt: str | None = None

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ):
        self.last_prompt = prompt
        yield self._reply


def _volume_with_chapters(db: Session) -> models.Volume:
    book = models.Book(title="测试之书", genre="玄幻", tone="热血", target_words=10000)
    db.add(book)
    db.commit()
    db.refresh(book)
    volume = models.Volume(
        book_id=book.id, title="第一卷", order_index=0, summary="原定大纲:主角踏上冒险"
    )
    db.add(volume)
    db.commit()
    db.refresh(volume)
    ch1 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第1章",
        order_index=0,
        outline="旧细纲1",
        status="done",
    )
    ch2 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第2章",
        order_index=1,
        outline="旧细纲2",
        status="outline",
    )
    ch1.content = models.ChapterContent(body="第一章正文")
    db.add_all([ch1, ch2])
    db.commit()
    db.refresh(ch1)
    db.add(
        models.ChapterSummary(
            chapter_id=ch1.id,
            summary="主角在第1章觉醒异能",
            source_hash=memory_sync.chapter_body_hash(ch1),
        )
    )
    db.commit()
    return volume


async def test_replan_missing_volume_yields_error(db):
    events = [
        ev async for ev in pipeline.replan_volume_chapters(db, "missing", "剧情走偏")
    ]
    assert events[0][0] == "error"


async def test_replan_skips_when_all_chapters_done(db, monkeypatch):
    book = models.Book(
        title="全部完成之书", genre="玄幻", tone="热血", target_words=10000
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    volume = models.Volume(book_id=book.id, title="第一卷", summary="大纲")
    db.add(volume)
    db.commit()
    db.refresh(volume)
    db.add(
        models.Chapter(
            book_id=book.id, volume_id=volume.id, title="第1章", status="done"
        )
    )
    db.commit()

    def _boom_llm():
        raise AssertionError("没有待规划章节时不应调用 LLM")

    monkeypatch.setattr(pipeline, "get_llm", _boom_llm)

    events = [
        ev async for ev in pipeline.replan_volume_chapters(db, volume.id, "剧情走偏")
    ]
    assert events[-1] == ("done", "")
    assert any("无需重新规划" in data for ev, data in events if ev == "log")


async def test_replan_only_rewrites_pending_chapter_outline(db, monkeypatch):
    volume = _volume_with_chapters(db)
    fake_llm = FakeStreamLLM("## 第2章新标题\n新细纲内容")
    monkeypatch.setattr(pipeline, "get_llm", lambda: fake_llm)

    events = [
        ev
        async for ev in pipeline.replan_volume_chapters(db, volume.id, "剧情已经走偏了")
    ]
    assert events[-1] == ("done", "")

    chapters = sorted(volume.chapters, key=lambda c: c.order_index)
    ch1, ch2 = chapters[0], chapters[1]
    db.refresh(ch1)
    db.refresh(ch2)
    # 已完成的第1章不受影响
    assert ch1.status == "done"
    assert ch1.outline == "旧细纲1"
    # 未完成的第2章被重新规划
    assert ch2.status == "outline"
    assert ch2.title == "第2章新标题"
    assert ch2.outline == "新细纲内容"
    # 分卷大纲本身不受影响
    db.refresh(volume)
    assert volume.summary == "原定大纲:主角踏上冒险"
    # 提示词里带上了已完成章节的摘要,保证续写连贯
    assert fake_llm.last_prompt is not None
    assert "主角在第1章觉醒异能" in fake_llm.last_prompt


async def test_replan_refuses_partial_result_without_modifying_any_outline(
    db, monkeypatch
):
    volume = _volume_with_chapters(db)
    additional = models.Chapter(
        book_id=volume.book_id,
        volume_id=volume.id,
        title="第3章",
        order_index=2,
        outline="旧细纲3",
        status="outline",
    )
    db.add(additional)
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", lambda: FakeStreamLLM("## 新章\n一项细纲"))

    events = [
        event
        async for event in pipeline.replan_volume_chapters(db, volume.id, "调整剧情")
    ]

    assert events[-1][0] == "error"
    assert "需要 2 项" in events[-1][1]
    assert [
        ch.outline for ch in sorted(volume.chapters, key=lambda c: c.order_index)
    ] == [
        "旧细纲1",
        "旧细纲2",
        "旧细纲3",
    ]


@pytest.mark.parametrize("change", ["content", "outline", "volume"])
async def test_replan_preserves_concurrent_edit_and_does_not_apply_partial_batch(
    db: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    volume = _volume_with_chapters(db)
    third = models.Chapter(
        book_id=volume.book_id,
        volume_id=volume.id,
        title="第3章",
        order_index=2,
        outline="旧细纲3",
        status="outline",
    )
    destination = models.Volume(book_id=volume.book_id, title="第二卷", order_index=1)
    db.add_all([third, destination])
    db.commit()
    volume_id, third_id, destination_id = volume.id, third.id, destination.id
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedModel:
        async def chat_stream(
            self, _prompt: str, system: str = ""
        ) -> AsyncIterator[str]:
            entered.set()
            await release.wait()
            yield "## 模型第2章\n模型细纲2\n## 模型第3章\n模型细纲3"

    async def collect() -> list[pipeline.Event]:
        return [
            event
            async for event in pipeline.replan_volume_chapters(
                db, volume_id, "调整剧情"
            )
        ]

    monkeypatch.setattr(pipeline, "get_llm", DelayedModel)
    generation = asyncio.create_task(collect())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with Session(db.get_bind()) as editor:
            if change == "content":
                chapter_api.update_chapter_content(
                    third_id,
                    schemas.ChapterContentUpdate(body="作者新保存的正文"),
                    editor,
                )
            elif change == "outline":
                chapter_api.update_chapter(
                    third_id,
                    schemas.ChapterUpdate(title="作者改名", outline="作者新细纲"),
                    editor,
                )
            else:
                chapter_api.update_chapter(
                    third_id,
                    schemas.ChapterUpdate(volume_id=destination_id, order_index=4),
                    editor,
                )
            revisions = {
                job.id: job.revision
                for job in editor.scalars(select(models.MemorySyncJob))
            }
    finally:
        release.set()

    events = await asyncio.wait_for(generation, 2)
    assert events[-1][0] == "error" and "生成期间待规划章节发生变动" in events[-1][1]
    with Session(db.get_bind()) as reader:
        unchanged = reader.scalar(
            select(models.Chapter).where(models.Chapter.order_index == 1)
        )
        assert unchanged is not None
        assert (unchanged.title, unchanged.outline, unchanged.status) == (
            "第2章",
            "旧细纲2",
            "outline",
        )
        edited = reader.get(models.Chapter, third_id)
        assert edited is not None
        if change == "content":
            assert edited.content and edited.content.body == "作者新保存的正文"
            assert (edited.title, edited.outline, edited.status) == (
                "第3章",
                "旧细纲3",
                "done",
            )
            assert revisions  # The author's save already enqueued memory work.
        elif change == "outline":
            assert (edited.title, edited.outline) == ("作者改名", "作者新细纲")
        else:
            assert (edited.volume_id, edited.order_index) == (destination_id, 4)
        assert {
            job.id: job.revision for job in reader.scalars(select(models.MemorySyncJob))
        } == revisions
