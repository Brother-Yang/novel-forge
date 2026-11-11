"""长篇规划的请求边界和工具参数契约，所有生成过程离线打桩。"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, schemas
from app.agents import pipeline, planner, stages, tools
from app.api.generate import router as generation_router
from app.database import get_db
from app.db_base import Base


@pytest.mark.parametrize(
    "schema,payload",
    [
        (schemas.BookCreate, {"title": "书"}),
        (schemas.BookUpdate, {}),
        (schemas.GenerateNovelReq, {"brief": "想法"}),
        (schemas.GenerateAgentReq, {"brief": "想法"}),
    ],
)
@pytest.mark.parametrize("target", [0, -1, schemas.NOVEL_TARGET_WORDS_MAX + 1])
def test_target_request_rejects_invalid_capacity(
    schema: type[BaseModel], payload: dict[str, object], target: int
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, "target_words": target})


@pytest.mark.parametrize(
    "target", [1_000_000, 2_000_000, schemas.NOVEL_TARGET_WORDS_MAX]
)
def test_long_target_request_is_preserved(target: int) -> None:
    request = schemas.GenerateNovelReq(brief="想法", target_words=target)
    assert request.target_words == target
    book = models.Book(title="书", target_words=target)
    planner.apply_plan_to_book(book, planner.clamp_plan({}, target_words=target))
    assert book.target_words == target


def test_request_accepts_max_volumes_and_rejects_unbounded_chapter_batch() -> None:
    assert (
        schemas.GenerateOutlineReq(book_id="book", volume_count=40).volume_count == 40
    )
    with pytest.raises(ValidationError):
        schemas.GenerateOutlineReq(book_id="book", volume_count=41)
    with pytest.raises(ValidationError):
        schemas.GenerateChapterOutlinesReq(volume_id="volume", chapter_count=21)
    assert schemas.BookUpdate().target_words is None


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/generate/characters", {"book_id": "book", "count": count})
        for count in (0, -1, schemas.CHARACTERS_PER_GENERATION_MAX + 1, 10**12)
    ]
    + [
        ("/generate/chapter", {"chapter_id": "chapter", "words": words})
        for words in (0, -1, schemas.CHAPTER_GENERATION_WORDS_MAX + 1, 10**12)
    ],
)
async def test_manual_generation_rejects_invalid_capacity_before_generation(
    path: str, payload: dict[str, object], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    app.include_router(generation_router)
    app.dependency_overrides[get_db] = lambda: db

    def unexpected_model_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid capacity reached model generation")

    monkeypatch.setattr(pipeline, "gen_characters", unexpected_model_call)
    monkeypatch.setattr(pipeline, "gen_chapter_body", unexpected_model_call)
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(path, json=payload)
    assert response.status_code == 422


def test_manual_generation_keeps_defaults_and_accepts_short_or_maximum_requests() -> (
    None
):
    assert schemas.GenerateCharactersReq(book_id="book").count == 5
    assert schemas.GenerateChapterReq(chapter_id="chapter").words == 2000
    for count in (1, schemas.CHARACTERS_PER_GENERATION_MAX):
        assert schemas.GenerateCharactersReq(book_id="book", count=count).count == count
    for words in (1, schemas.CHAPTER_GENERATION_WORDS_MAX):
        assert (
            schemas.GenerateChapterReq(chapter_id="chapter", words=words).words == words
        )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/scale.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


async def test_outline_tool_passes_all_long_plan_volumes(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = models.Book(title="长篇", target_words=2_000_000)
    db.add(book)
    db.commit()
    received: list[int] = []

    async def outline(
        _db: Session,
        _book_id: str,
        volume_count: int,
        *,
        recovery: stages.Recovery,
    ) -> AsyncIterator[tuple[str, str]]:
        received.append(volume_count)
        yield "done", ""

    monkeypatch.setattr(pipeline, "gen_outline", outline)
    result = await tools.tool_create_outline(db, book.id, {"volume_count": 40})
    assert result["ok"] is True
    assert received == [40]


@pytest.mark.parametrize("count", [0, 41])
async def test_outline_tool_rejects_excess_instead_of_silently_clamping(
    db: Session, count: int
) -> None:
    book = models.Book(title="书")
    db.add(book)
    db.commit()
    with pytest.raises(tools.ToolError, match="volume_count"):
        await tools.tool_create_outline(db, book.id, {"volume_count": count})


async def test_chapter_tool_requires_bounded_batch_and_keeps_volume(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = models.Book(title="书")
    volume = models.Volume(book=book, title="第一卷")
    db.add(volume)
    db.commit()
    received: list[int] = []

    async def chapters(
        _db: Session,
        _volume_id: str,
        chapter_count: int,
        *,
        recovery: stages.Recovery,
    ) -> AsyncIterator[tuple[str, str]]:
        received.append(chapter_count)
        yield "done", ""

    monkeypatch.setattr(pipeline, "gen_chapter_outlines", chapters)
    with pytest.raises(tools.ToolError, match="增加卷数"):
        await tools.tool_create_chapter_outlines(
            db, book.id, {"volume_id": volume.id, "chapter_count": 21}
        )
    assert received == []
    result = await tools.tool_create_chapter_outlines(
        db, book.id, {"volume_id": volume.id, "chapter_count": 20}
    )
    assert result["ok"] is True
    assert received == [20]
