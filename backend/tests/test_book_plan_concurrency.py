"""A model plan never replaces metadata changed through the book API."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.agents import orchestrator, pipeline, planner
from app.api import books
from app.database import get_db
from app.db_base import Base
from app.tasks import agent_generation, book_generation, novel_generation


@pytest.mark.parametrize("task_type", ["novel", "agent"])
@pytest.mark.parametrize(
    "changes",
    [
        {
            "title": "作者改过的书名",
            "intro": "作者改过的简介",
            "brief": "作者改过的创作想法",
            "genre": "科幻",
            "tone": "现实",
        },
        {"target_words": 60_000},
    ],
)
async def test_book_edit_during_planning_rejects_the_whole_model_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    changes: dict[str, str | int],
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'book_plan.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(books.router)

    def session_for_request() -> Iterator[Session]:
        with sessions() as db:
            yield db

    def forbid_generation() -> None:
        raise AssertionError("冲突后不得调用下一阶段的模型")

    app.dependency_overrides[get_db] = session_for_request
    monkeypatch.setattr(orchestrator, "supports_tool_calling", lambda: True)
    monkeypatch.setattr(orchestrator, "get_llm", forbid_generation)
    monkeypatch.setattr(pipeline, "get_llm", forbid_generation)
    original = {
        "title": "未命名",
        "intro": "",
        "brief": "原始创作想法",
        "genre": "",
        "tone": "",
        "target_words": 12_000,
        "key_elements": '["已有规划要点"]',
    }
    try:
        with sessions() as db:
            book = models.Book(**original)
            db.add(book)
            db.flush()
            task = models.GenerationTask(
                book_id=book.id, type=task_type, status="running"
            )
            db.add(task)
            db.commit()
            book_id, task_id = book.id, task.id

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:

                async def delayed_plan(
                    brief: str, **kwargs: object
                ) -> planner.NovelPlan:
                    assert brief == original["brief"]
                    assert kwargs["target_words"] == original["target_words"]
                    assert not db.in_transaction()
                    edited = await client.patch(f"/books/{book_id}", json=changes)
                    assert edited.status_code == 200
                    return planner.NovelPlan(
                        title="模型书名",
                        genre="模型题材",
                        tone="模型基调",
                        intro="模型简介",
                        volume_count=2,
                        chapters_per_volume=3,
                        words_per_chapter=2000,
                        character_count=3,
                        key_elements=["来自旧创作想法的生成要点"],
                    )

                monkeypatch.setattr(planner, "plan_novel", delayed_plan)
                if task_type == "novel":
                    await novel_generation.run_novel_task(db, task)
                else:
                    await agent_generation.run_agent_task(db, task)

                latest = await client.get(f"/books/{book_id}")
                assert latest.status_code == 200
                assert {
                    key: latest.json()[key] for key in original
                } == original | changes

        with sessions() as db:
            saved_task = db.get(models.GenerationTask, task_id)
            assert saved_task is not None and saved_task.status == "error"
            assert "规划期间书籍信息已修改" in saved_task.error
            assert saved_task.progress == 0
            assert "volume_count" not in book_generation.load_checkpoint(saved_task)
    finally:
        engine.dispose()
