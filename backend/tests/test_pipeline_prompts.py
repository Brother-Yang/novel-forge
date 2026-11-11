"""生成管线必须把用户创作想法喂给各层模型,不能只靠书名/空简介。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents import pipeline
from app.db_base import Base


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/pipeline_prompts_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class FakeStreamLLM:
    def __init__(self, reply: str) -> None:
        super().__init__()
        self._reply = reply
        self.last_prompt: str | None = None

    async def chat_stream(self, prompt: str, system: str = "", temperature: float = 0.8):
        self.last_prompt = prompt
        yield self._reply


async def test_worldbuilding_prompt_includes_user_brief(db, monkeypatch):
    brief = "AI时代，中年程序员靠古法编程力挽狂澜"
    book = models.Book(
        title="未命名",
        brief=brief,
        intro="",
        genre="",
        tone="",
        target_words=10000,
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    fake = FakeStreamLLM("## 职场规则\n大模型外包横行。\n## 环境\n旧机房\n## 组织\n维护小组\n## 矛盾\n系统停摆")
    monkeypatch.setattr(pipeline, "get_llm", lambda: fake)

    async def _skip_index(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(pipeline.memory, "index_worldbuilding", _skip_index)

    events = [ev async for ev in pipeline.gen_worldbuilding(db, book.id)]
    assert events[-1] == ("done", "")
    assert fake.last_prompt is not None
    assert brief in fake.last_prompt
