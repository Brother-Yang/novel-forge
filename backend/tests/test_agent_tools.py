"""智能体工具层测试:幂等保护(已存在则跳过)、参数/归属校验、状态聚合。

使用真实的临时 SQLite 数据库(而非 mock),但对会触发真实 LLM 调用的
pipeline 生成函数打桩,保证测试离线、快速、不依赖网络。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents import pipeline, tools
from app.db_base import Base


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/tools_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _book(db: Session, **kwargs: object) -> models.Book:
    book = models.Book(
        title="测试之书", genre="玄幻", tone="热血", target_words=10000, **kwargs
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def _forbid_llm() -> None:
    raise AssertionError("完整阶段不应调用模型")


# ---------- 只有完整结构才幂等跳过 ----------
async def test_write_worldbuilding_skips_if_complete(db, monkeypatch):
    book = _book(db)
    db.add_all([
        models.Worldbuilding(book_id=book.id, title=f"已有设定{i}", md_content="设定正文")
        for i in range(4)
    ])
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", _forbid_llm)
    result = await tools.tool_write_worldbuilding(db, book.id, {})
    assert result["skipped"] is True
    assert result["message"] == "已保存 4 条完整设定"


async def test_create_characters_skips_if_complete(db, monkeypatch):
    book = _book(db)
    db.add_all([
        models.Character(book_id=book.id, name=f"已有人物{i}", md_content="旧人物卡")
        for i in range(3)
    ])
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", _forbid_llm)
    result = await tools.tool_create_characters(db, book.id, {"count": 3})
    assert result["skipped"] is True
    assert result["message"] == "已保存 3 个完整人物"


async def test_create_outline_skips_only_after_both_parts_complete(db, monkeypatch):
    book = _book(db)
    db.add(models.Volume(book_id=book.id, title="已有卷", summary="梗概"))
    db.add(models.PlotThread(book_id=book.id, name="已有伏笔", description="伏笔描述"))
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", _forbid_llm)
    result = await tools.tool_create_outline(db, book.id, {"volume_count": 1})
    assert result["skipped"] is True


async def test_create_chapter_outlines_skips_if_volume_is_complete(db, monkeypatch):
    book = _book(db)
    volume = models.Volume(book_id=book.id, title="第一卷")
    db.add(volume)
    db.commit()
    db.add(models.Chapter(book_id=book.id, volume_id=volume.id, title="第1章", outline="旧细纲"))
    db.commit()
    monkeypatch.setattr(pipeline, "get_llm", _forbid_llm)
    result = await tools.tool_create_chapter_outlines(
        db, book.id, {"volume_id": volume.id, "chapter_count": 1}
    )
    assert result["skipped"] is True


# ---------- 参数/归属校验:非法或越权引用应转成 ToolError,而不是崩溃/串书 ----------
async def test_create_chapter_outlines_rejects_unknown_volume(db):
    book = _book(db)
    with pytest.raises(tools.ToolError):
        await tools.tool_create_chapter_outlines(
            db, book.id, {"volume_id": 999999, "chapter_count": 5}
        )


async def test_create_chapter_outlines_rejects_volume_from_other_book(db):
    book_a = _book(db)
    book_b = _book(db)
    volume_b = models.Volume(book_id=book_b.id, title="属于 B 的卷")
    db.add(volume_b)
    db.commit()
    db.refresh(volume_b)

    with pytest.raises(tools.ToolError):
        await tools.tool_create_chapter_outlines(
            db, book_a.id, {"volume_id": volume_b.id, "chapter_count": 5}
        )


async def test_write_chapter_rejects_chapter_from_other_book(db):
    book_a = _book(db)
    book_b = _book(db)
    chapter_b = models.Chapter(book_id=book_b.id, title="属于 B 的章")
    db.add(chapter_b)
    db.commit()
    db.refresh(chapter_b)

    with pytest.raises(tools.ToolError):
        await tools.tool_write_chapter(db, book_a.id, {"chapter_id": chapter_b.id, "words": 2000})


async def test_write_chapter_rejects_non_integer_chapter_id(db):
    book = _book(db)
    with pytest.raises(tools.ToolError):
        await tools.tool_write_chapter(db, book.id, {"chapter_id": "abc", "words": 2000})


# ---------- 高风险操作护栏:改写已完成章节需先经 review_content 批准 ----------
async def test_write_chapter_blocks_rewriting_done_chapter_without_approval(db, monkeypatch):
    book = _book(db)
    chapter = models.Chapter(book_id=book.id, title="第1章", status="done", word_count=500)
    chapter.content = models.ChapterContent(body="已有完成正文")
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    monkeypatch.setattr(pipeline, "get_llm", _forbid_llm)

    with pytest.raises(tools.ToolError, match="review_content"):
        await tools.tool_write_chapter(db, book.id, {"chapter_id": chapter.id, "words": 2000})


async def test_write_chapter_allows_rewrite_once_after_revision_approved(db, monkeypatch):
    book = _book(db)
    chapter = models.Chapter(
        book_id=book.id, title="第1章", status="done", word_count=500, revision_approved=True
    )
    chapter.content = models.ChapterContent(body="已有完成正文")
    db.add(chapter)
    db.commit()
    db.refresh(chapter)

    class FakeModel:
        async def chat_stream(self, prompt: str, *, system: str) -> AsyncIterator[str]:
            yield "重新生成的正文"

    monkeypatch.setattr(pipeline, "get_llm", FakeModel)
    monkeypatch.setattr(
        pipeline.memory, "build_chapter_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        pipeline.memory_sync, "process_chapter_memory", AsyncMock(return_value=True)
    )

    result = await tools.tool_write_chapter(db, book.id, {"chapter_id": chapter.id, "words": 2000})
    assert result["ok"] is True
    db.refresh(chapter)
    # 消费一次批准后立即复位,不能无限期重写
    assert chapter.revision_approved is False
    content = chapter.content
    assert content is not None and content.body == "重新生成的正文"
    with pytest.raises(tools.ToolError, match="review_content"):
        await tools.tool_write_chapter(db, book.id, {"chapter_id": chapter.id})


# ---------- 状态感知:聚合结果应反映真实数据库状态 ----------
async def test_get_book_state_aggregates_progress(db):
    book = _book(db)
    volume = models.Volume(book_id=book.id, title="第一卷")
    db.add(volume)
    db.commit()
    db.refresh(volume)
    chapter1 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第1章",
        status="done",
        word_count=1000,
        order_index=0,
    )
    chapter1.content = models.ChapterContent(body="已经保存的正文")
    chapter2 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第2章",
        status="outline",
        word_count=0,
        order_index=1,
    )
    db.add(chapter1)
    db.add(chapter2)
    db.add(models.PlotThread(book_id=book.id, name="线程A", status="open"))
    db.commit()
    db.refresh(chapter1)
    db.refresh(chapter2)

    state = await tools.tool_get_book_state(db, book.id, {})
    assert state["chapters_total"] == 2
    assert state["chapters_done"] == 1
    assert state["total_word_count"] == 1000
    assert state["volumes"] == [
        {
            "volume_id": volume.id,
            "title": "第一卷",
            "chapter_count": 2,
            "chapters_done": 1,
            "chapters": [
                {
                    "id": chapter1.id,
                    "title": "第1章",
                    "order_index": 0,
                    "status": "done",
                    "complete": True,
                },
                {
                    "id": chapter2.id,
                    "title": "第2章",
                    "order_index": 1,
                    "status": "outline",
                    "complete": False,
                },
            ],
        }
    ]
    assert state["open_plot_threads"] == [{"name": "线程A", "status": "open"}]
    page = await tools.tool_get_book_state(db, book.id, {"chapter_offset": 1})
    assert page["chapter_window"] == {
        "offset": 1,
        "limit": 32,
        "total": 2,
        "next_offset": None,
        "truncated": True,
        "order": "pending_first",
    }
    volumes = page["volumes"]
    assert isinstance(volumes, list)
    assert [row["id"] for row in volumes[0]["chapters"]] == [chapter1.id]


async def test_get_book_state_rejects_invalid_chapter_offset(db):
    book = _book(db)
    with pytest.raises(tools.ToolError, match="非负整数"):
        await tools.tool_get_book_state(db, book.id, {"chapter_offset": -1})


async def test_get_book_state_missing_book_raises(db):
    with pytest.raises(tools.ToolError):
        await tools.tool_get_book_state(db, "no-such-book", {})


# ---------- review_content:内容为空时应直接要求修改,不需要调用 LLM ----------
async def test_review_content_empty_target_needs_revision_without_llm(db, monkeypatch):
    book = _book(db)
    volume = models.Volume(book_id=book.id, title="空卷", summary="")
    db.add(volume)
    db.commit()
    db.refresh(volume)

    def _boom_llm():
        raise AssertionError("内容为空时不应调用 LLM")

    monkeypatch.setattr(tools, "get_llm", _boom_llm)
    result = await tools.tool_review_content(
        db, book.id, {"target_type": "outline", "target_id": volume.id}
    )
    assert result["needs_revision"] is True


async def test_review_content_unknown_target_type_raises(db):
    book = _book(db)
    with pytest.raises(tools.ToolError):
        await tools.tool_review_content(
            db, book.id, {"target_type": "unknown", "target_id": 1}
        )


class _FakeReviewLLM:
    def __init__(self, reply: str) -> None:
        super().__init__()
        self._reply = reply

    async def chat(self, prompt: str, system: str = "", temperature: float = 0.8) -> str:
        return self._reply


async def test_review_content_needs_revision_approves_rewrite_of_done_chapter(db, monkeypatch):
    book = _book(db)
    chapter = models.Chapter(
        book_id=book.id, title="第1章", status="done", word_count=500
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    db.add(models.ChapterContent(chapter_id=chapter.id, body="正文内容"))
    db.commit()

    fake_llm = _FakeReviewLLM('{"needs_revision": true, "feedback": "人物崩人设"}')
    monkeypatch.setattr(tools, "get_llm", lambda: fake_llm)

    result = await tools.tool_review_content(
        db, book.id, {"target_type": "chapter", "target_id": chapter.id}
    )
    assert result["needs_revision"] is True
    db.refresh(chapter)
    assert chapter.revision_approved is True


async def test_review_content_no_revision_does_not_approve(db, monkeypatch):
    book = _book(db)
    chapter = models.Chapter(book_id=book.id, title="第1章", status="done", word_count=500)
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    db.add(models.ChapterContent(chapter_id=chapter.id, body="正文内容"))
    db.commit()

    fake_llm = _FakeReviewLLM('{"needs_revision": false, "feedback": "还不错"}')
    monkeypatch.setattr(tools, "get_llm", lambda: fake_llm)

    await tools.tool_review_content(
        db, book.id, {"target_type": "chapter", "target_id": chapter.id}
    )
    db.refresh(chapter)
    assert chapter.revision_approved is False


# ---------- replan_volume:参数/归属校验 ----------
async def test_replan_volume_rejects_unknown_volume(db):
    book = _book(db)
    with pytest.raises(tools.ToolError):
        await tools.tool_replan_volume(
            db, book.id, {"volume_id": 999999, "reason": "剧情走偏"}
        )


async def test_replan_volume_requires_non_empty_reason(db):
    book = _book(db)
    volume = models.Volume(book_id=book.id, title="第一卷")
    db.add(volume)
    db.commit()
    db.refresh(volume)
    with pytest.raises(tools.ToolError):
        await tools.tool_replan_volume(db, book.id, {"volume_id": volume.id, "reason": "  "})


# ---------- dispatch:未知工具名应转成 ToolError ----------
async def test_dispatch_unknown_tool_raises(db):
    book = _book(db)
    with pytest.raises(tools.ToolError):
        await tools.dispatch(db, book.id, "no_such_tool", {})


async def test_dispatch_finish_book_routes_correctly(db):
    book = _book(db)
    chapter = models.Chapter(book_id=book.id, title="终章", status="done")
    chapter.content = models.ChapterContent(body="结局正文")
    db.add(chapter)
    db.commit()
    result = await tools.dispatch(db, book.id, "finish_book", {"reason": "写完了"})
    assert result == {"ok": True, "reason": "写完了"}
