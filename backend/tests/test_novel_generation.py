"""全书编排器规划阶段:用户原话不能当成书名,规划结果要写回书籍。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents import planner
from app.db_base import Base
from app.tasks import novel_generation


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/novel_generation_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


async def test_ensure_plan_does_not_treat_brief_snippet_as_user_title(db, monkeypatch):
    brief = "AI时代，中年程序员靠古法编程力挽狂澜"
    book = models.Book(
        title=planner.fallback_title(brief),
        brief=brief,
        intro="",
        genre="",
        tone="",
        target_words=12000,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    task = models.GenerationTask(
        book_id=book.id,
        type="novel",
        status="running",
        progress=0,
        checkpoint_json="{}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    captured: dict[str, str] = {}

    async def fake_plan_novel(text: str, **kwargs: object) -> planner.NovelPlan:
        captured["title"] = str(kwargs.get("title") or "")
        captured["brief"] = text
        return planner.NovelPlan(
            title="古法残编",
            genre="都市",
            tone="讽刺",
            intro="中年程序员在 AI 浪潮中用古法编程杀出一条路。",
            volume_count=2,
            chapters_per_volume=3,
            words_per_chapter=2000,
            character_count=3,
        )

    monkeypatch.setattr(planner, "plan_novel", fake_plan_novel)

    await novel_generation._ensure_plan(db, task)

    db.refresh(book)
    assert captured["title"] == ""
    assert captured["brief"] == brief
    assert book.title == "古法残编"
    assert book.intro == "中年程序员在 AI 浪潮中用古法编程杀出一条路。"
    assert book.brief == brief
    assert book.genre == "都市"
