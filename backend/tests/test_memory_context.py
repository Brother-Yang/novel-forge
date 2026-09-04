"""app/memory/context.py::refresh_volume_summary 测试:基于已完成章节的
ChapterSummary 重新提炼卷摘要,不依赖真实 LLM(打桩)。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.db_base import Base
from app.memory import context as memory_context


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/memory_context_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.last_prompt: str | None = None

    async def chat(self, prompt: str, system: str = "", temperature: float = 0.8) -> str:
        self.last_prompt = prompt
        return self._reply


def _book_with_volume(db: Session) -> models.Volume:
    book = models.Book(title="测试之书", genre="玄幻", tone="热血", target_words=10000)
    db.add(book)
    db.commit()
    db.refresh(book)
    volume = models.Volume(book_id=book.id, title="第一卷", order_index=0, summary="旧摘要")
    db.add(volume)
    db.commit()
    db.refresh(volume)
    return volume


async def test_refresh_volume_summary_missing_volume_is_noop(db):
    await memory_context.refresh_volume_summary(db, 999)  # 不应抛异常


async def test_refresh_volume_summary_noop_without_done_chapters(db, monkeypatch):
    volume = _book_with_volume(db)
    db.add(
        models.Chapter(
            book_id=volume.book_id,
            volume_id=volume.id,
            title="第1章",
            status="outline",  # 未完成
        )
    )
    db.commit()

    def _boom_llm():
        raise AssertionError("没有已完成章节时不应调用 LLM")

    monkeypatch.setattr(memory_context, "get_llm", _boom_llm)

    await memory_context.refresh_volume_summary(db, volume.id)

    db.refresh(volume)
    assert volume.summary == "旧摘要"


async def test_refresh_volume_summary_rewrites_from_chapter_summaries(db, monkeypatch):
    volume = _book_with_volume(db)
    chapter = models.Chapter(
        book_id=volume.book_id, volume_id=volume.id, title="第1章", status="done"
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    db.add(models.ChapterSummary(chapter_id=chapter.id, summary="主角觉醒了异能"))
    db.commit()

    fake_llm = FakeLLM("新的卷摘要:主角踏上冒险之路")
    monkeypatch.setattr(memory_context, "get_llm", lambda: fake_llm)

    await memory_context.refresh_volume_summary(db, volume.id)

    db.refresh(volume)
    assert volume.summary == "新的卷摘要:主角踏上冒险之路"
    assert fake_llm.last_prompt is not None
    assert "主角觉醒了异能" in fake_llm.last_prompt
    assert "第一卷" in fake_llm.last_prompt


async def test_refresh_volume_summary_skips_chapters_without_summary(db, monkeypatch):
    volume = _book_with_volume(db)
    db.add(
        models.Chapter(
            book_id=volume.book_id, volume_id=volume.id, title="第1章", status="done"
        )
    )  # 无 ChapterSummary
    db.commit()

    def _boom_llm():
        raise AssertionError("没有可用摘要时不应调用 LLM")

    monkeypatch.setattr(memory_context, "get_llm", _boom_llm)

    await memory_context.refresh_volume_summary(db, volume.id)

    db.refresh(volume)
    assert volume.summary == "旧摘要"


async def test_chapter_context_includes_creative_brief(db, monkeypatch):
    brief = "AI时代，中年程序员靠古法编程力挽狂澜"
    book = models.Book(
        title="古法残编",
        brief=brief,
        intro="一部都市讽刺小说",
        genre="都市",
        tone="讽刺",
        target_words=10000,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    chapter = models.Chapter(
        book_id=book.id, title="第1章", outline="主角在裁员会上掏出纸笔", status="outline"
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)

    class BoomLLM:
        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("测试不走向量检索")

    monkeypatch.setattr(memory_context, "get_llm", BoomLLM)

    ctx = await memory_context.build_chapter_context(db, chapter)
    assert brief in ctx
    assert "一部都市讽刺小说" in ctx
