"""PATCH omission and null semantics against real routes and temporary SQLite."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.api import books, chapters, characters, volumes, worldbuilding
from app.database import get_db
from app.db_base import Base


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Session]]:
    engine = create_engine(
        f"sqlite:///{tmp_path}/patch.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            book = models.Book(id="book", title="原书名", intro="原简介")
            volume = models.Volume(id="volume", book=book, title="原卷", order_index=3)
            db.add_all(
                [
                    book,
                    volume,
                    models.Chapter(
                        id="chapter",
                        book=book,
                        volume=volume,
                        title="原章",
                        outline="原章纲",
                    ),
                    models.Character(
                        id="character",
                        book=book,
                        name="原人物",
                        state_json='{"location":"家"}',
                    ),
                    models.Worldbuilding(
                        id="world",
                        book_id=book.id,
                        title="原设定",
                        md_content="原设定内容",
                    ),
                ]
            )
            db.commit()
            app = FastAPI()
            for router in (
                books.router,
                volumes.router,
                chapters.router,
                characters.router,
                worldbuilding.router,
            ):
                app.include_router(router)

            def current_session() -> Iterator[Session]:
                yield db

            app.dependency_overrides[get_db] = current_session
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://test"
            ) as client:
                yield client, db
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "path,field,original,other_field,other_value",
    [
        ("/books/book", "title", "原书名", "intro", "原简介"),
        ("/volumes/volume", "order_index", 3, "title", "原卷"),
        ("/chapters/chapter", "outline", "原章纲", "title", "原章"),
        ("/characters/character", "state_json", '{"location":"家"}', "name", "原人物"),
        ("/worldbuilding/world", "md_content", "原设定内容", "title", "原设定"),
    ],
)
async def test_invalid_null_is_rejected_without_saving_other_fields(
    api: tuple[AsyncClient, Session],
    path: str,
    field: str,
    original: str | int,
    other_field: str,
    other_value: str,
) -> None:
    client, db = api
    invalid = await client.patch(path, json={field: None, other_field: "不应写入"})
    assert invalid.status_code == 422
    detail = invalid.json()["detail"][0]
    assert detail["loc"] == ["body", field]
    assert detail["input"] is None
    assert db.scalars(select(models.MemorySyncJob)).all() == []
    unchanged = await client.patch(path, json={})
    assert unchanged.status_code == 200
    assert unchanged.json()[field] == original
    assert unchanged.json()[other_field] == other_value


async def test_volume_can_be_cleared_and_empty_text_is_distinct_from_null(
    api: tuple[AsyncClient, Session],
) -> None:
    client, db = api
    response = await client.patch(
        "/chapters/chapter", json={"volume_id": None, "outline": ""}
    )
    assert response.status_code == 200
    assert response.json()["volume_id"] is None
    assert response.json()["outline"] == ""
    assert response.json()["title"] == "原章"
    db.expire_all()
    chapter = db.get(models.Chapter, "chapter")
    assert chapter is not None and chapter.volume_id is None and chapter.outline == ""
