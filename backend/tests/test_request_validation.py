import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app import main, models
from app.config import Settings
from app.database import get_db
from app.db_base import Base


@pytest.fixture
async def editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, Engine]]:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'request-validation.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = models.Book(id="book", title="合成书")
        db.add_all(
            [
                models.Chapter(id="chapter", book=book, title="第一章", order_index=4),
                models.Character(
                    id="character", book=book, name="原人物", md_content="原人物卡"
                ),
            ]
        )
        db.commit()

    def session() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            app_env="local",
            access_password="",
            database_url=database_url,
            chroma_dir=str(tmp_path / "vectors"),
            cors_allowed_origins="http://test",
        ),
    )
    app = main.create_app()
    app.dependency_overrides[get_db] = session
    # This suite exercises the normal router and error middleware without
    # starting workers that would consume the memory jobs under test.
    app.state.runtime_ready = True
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, engine
    finally:
        engine.dispose()


def _memory_jobs(engine: Engine) -> set[tuple[str, str, str, str]]:
    with Session(engine) as db:
        return {
            (job.id, job.revision, job.source_hash, job.status)
            for job in db.scalars(select(models.MemorySyncJob))
        }


async def test_validation_errors_omit_request_values_and_keep_saved_content(
    editor: tuple[AsyncClient, Engine],
) -> None:
    client, engine = editor
    path = "/api/chapters/chapter/content"
    original = await client.put(path, json={"body": "原稿正文 🌙 e\u0301"})
    assert original.status_code == 200
    original_content = (await client.get(path)).json()
    original_jobs = _memory_jobs(engine)
    assert original_jobs

    cases: list[tuple[str, str, bytes, list[str | int]]] = [
        ("PUT", path, b'{"body":"private draft\\ud800"}', ["body", "body"]),
        ("PUT", path, b'{"body":["private draft","\\ud800"]}', ["body", "body"]),
        (
            "PUT",
            path,
            b'{"body":"private draft","expected_version":"\\ud800"}',
            ["body", "expected_version"],
        ),
        (
            "POST",
            "/api/chapters/reorder",
            b'{"items":[{"id":"chapter","order_index":NaN}]}',
            ["body", "items", 0, "order_index"],
        ),
        (
            "POST",
            "/api/chapters/reorder",
            b'{"items":[{"id":"\\ud800","order_index":0}]}',
            ["body", "items", 0, "id"],
        ),
        ("POST", "/api/books", b'{"title":"private draft\\ud800"}', ["body", "title"]),
        (
            "PATCH",
            "/api/chapters/chapter",
            b'{"title":"private draft\\ud800"}',
            ["body", "title"],
        ),
        (
            "PATCH",
            "/api/characters/character",
            b'{"md_content":"private draft\\ud800"}',
            ["body", "md_content"],
        ),
        (
            "POST",
            "/api/generate/novel",
            b'{"brief":"private draft\\ud800"}',
            ["body", "brief"],
        ),
    ]
    for method, url, body, location in cases:
        response = await client.request(
            method, url, content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        details = response.json()["detail"]
        assert isinstance(details, list) and details
        assert all(set(error) == {"loc", "msg", "type"} for error in details)
        assert any(error["loc"] == location for error in details)
        assert "private draft" not in response.text

    assert (await client.get(path)).json() == original_content
    assert _memory_jobs(engine) == original_jobs
    with Session(engine) as db:
        chapter = db.get(models.Chapter, "chapter")
        assert (
            chapter is not None
            and chapter.order_index == 4
            and chapter.status == "done"
        )
        assert chapter.title == "第一章"
        character = db.get(models.Character, "character")
        assert character is not None and character.md_content == "原人物卡"
        assert db.scalars(select(models.Book.id)).all() == ["book"]
        assert db.scalars(select(models.GenerationTask.id)).all() == []

    # A valid JSON surrogate pair still decodes to its original Unicode scalar.
    new_body = "改后的正文 🧭 e\u0301\n保留 NUL\0分隔符"
    payload = json.dumps(
        {"body": new_body, "expected_version": original_content["version"]}
    )
    saved = await client.put(
        path, content=payload, headers={"Content-Type": "application/json"}
    )
    assert saved.status_code == 200
    assert saved.json()["body"] == new_body
    assert saved.json()["version"] != original_content["version"]
    assert _memory_jobs(engine) != original_jobs
