from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app import models, schemas
from app.agents import orchestrator, pipeline, tools
from app.api.chapters import update_chapter_content
from app.config import settings
from app.db_base import Base
from app.llm.base import ToolCall, ToolChatResult


class ScriptedRevisionModel:
    def __init__(self, calls: list[ToolCall]) -> None:
        super().__init__()
        self.calls = iter(calls)
        self.on_review: Callable[[], None] | None = None
        self.reviews = 0
        self.writes = 0

    async def chat_with_tools(self, *args: object, **kwargs: object) -> ToolChatResult:
        return ToolChatResult(content="", tool_calls=[next(self.calls)])

    async def chat(self, *args: object, **kwargs: object) -> str:
        self.reviews += 1
        if self.on_review is not None:
            self.on_review()
        return '{"needs_revision": true, "feedback": "建议重写评审时的正文"}'

    async def chat_stream(self, *args: object, **kwargs: object) -> AsyncIterator[str]:
        self.writes += 1
        yield f"模型重写的正文{self.writes}"


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=name, name=name, arguments=arguments)


@pytest.fixture
def manuscript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Engine, str, str]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-review.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = models.Book(title="已命名的书")
        chapter = models.Chapter(
            book=book,
            title="已完成章节",
            status="done",
            word_count=7,
            revision_approved=True,
            content=models.ChapterContent(body="作者原本的正文"),
        )
        db.add(chapter)
        db.flush()
        book_id, chapter_id = book.id, chapter.id
        db.commit()

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(
        pipeline.memory, "build_chapter_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        pipeline.memory_sync, "process_chapter_memory", AsyncMock(return_value=True)
    )
    try:
        yield engine, book_id, chapter_id
    finally:
        engine.dispose()


def _use_model(monkeypatch: pytest.MonkeyPatch, model: ScriptedRevisionModel) -> None:
    for module in (orchestrator, tools, pipeline):
        monkeypatch.setattr(module, "get_llm", lambda: model)


@pytest.mark.parametrize("max_rewrites", [0, 1])
async def test_agent_rewrite_budget_applies_to_actual_writes(
    manuscript: tuple[Engine, str, str],
    monkeypatch: pytest.MonkeyPatch,
    max_rewrites: int,
) -> None:
    engine, book_id, chapter_id = manuscript
    monkeypatch.setattr(settings, "agent_max_rewrites", max_rewrites)
    model = ScriptedRevisionModel(
        [
            _call("review_content", target_type="chapter", target_id=chapter_id),
            _call("write_chapter", chapter_id=chapter_id),
            _call("review_content", target_type="chapter", target_id=f" {chapter_id} "),
            _call("write_chapter", chapter_id=chapter_id),
            _call("finish_book"),
        ]
    )
    _use_model(monkeypatch, model)
    with Session(engine) as db:
        events = [event async for event in orchestrator.run_agent(db, book_id)]
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None and chapter.content is not None
        assert chapter.content.body == (
            "模型重写的正文1" if max_rewrites else "作者原本的正文"
        )
        assert chapter.status == "done" and chapter.revision_approved is False
    assert model.reviews == model.writes == max_rewrites
    assert events[-1] == ("done", "")
    assert any(
        kind == "observation" and "已达最大重写次数" in data for kind, data in events
    )
    assert any(
        kind == "observation" and "请先调用 review_content" in data
        for kind, data in events
    )


@pytest.mark.parametrize("timing", ["during_review", "after_approval"])
async def test_author_save_invalidates_approval_for_reviewed_body(
    manuscript: tuple[Engine, str, str], monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:
    engine, book_id, chapter_id = manuscript
    monkeypatch.setattr(settings, "agent_max_rewrites", 2)
    model = ScriptedRevisionModel(
        [
            _call("review_content", target_type="chapter", target_id=chapter_id),
            _call("write_chapter", chapter_id=chapter_id),
            _call("finish_book"),
        ]
    )
    _use_model(monkeypatch, model)

    def save_author_edit() -> None:
        with Session(engine) as author:
            saved = update_chapter_content(
                chapter_id,
                schemas.ChapterContentUpdate(body="作者刚保存的新正文"),
                author,
            )
            assert saved.status == "done"

    if timing == "during_review":
        model.on_review = save_author_edit

    with Session(engine) as db:
        observations: list[str] = []
        async for kind, data in orchestrator.run_agent(db, book_id):
            if kind == "observation":
                observations.append(data)
                if timing == "after_approval" and '"needs_revision": true' in data:
                    save_author_edit()
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None and chapter.content is not None
        assert chapter.content.body == "作者刚保存的新正文"
        assert chapter.status == "done" and chapter.revision_approved is False
    assert model.reviews == 1 and model.writes == 0
    assert any("请先调用 review_content" in data for data in observations)
    if timing == "during_review":
        assert any("评审期间正文已修改" in data for data in observations)


async def test_review_cannot_grant_approval_after_tool_budget_is_exhausted(
    manuscript: tuple[Engine, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, book_id, chapter_id = manuscript
    monkeypatch.setattr(settings, "agent_max_tool_calls", 0)
    model = ScriptedRevisionModel(
        [
            _call("get_book_state"),
            _call("review_content", target_type="chapter", target_id=chapter_id),
        ]
    )
    _use_model(monkeypatch, model)
    with Session(engine) as db:
        events = [event async for event in orchestrator.run_agent(db, book_id)]
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None and chapter.content is not None
        assert chapter.revision_approved is False
        assert chapter.content.body == "作者原本的正文"
    assert model.reviews == model.writes == 0
    assert events[-1][0] == "error" and "最大工具调用次数" in events[-1][1]
    assert any(kind == "decision" and "get_book_state" in data for kind, data in events)
    assert not any(
        kind == "decision" and "review_content" in data for kind, data in events
    )
