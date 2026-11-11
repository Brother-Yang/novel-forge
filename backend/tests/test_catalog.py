"""Catalog projections, stable order and atomic updates in a real SQLite database."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import database, models
from app.api import books, chapters
from tests.memory_support import storage as catalog_storage_fixture  # noqa: F401


@pytest.fixture
async def catalog_client(
    storage: sessionmaker[Session],
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(books.router)
    app.include_router(chapters.router)

    def get_db() -> Iterator[Session]:
        with storage() as db:
            yield db

    app.dependency_overrides[database.get_db] = get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def seed_catalog(db: Session) -> tuple[str, list[models.Volume], list[models.Chapter]]:
    book = models.Book(title="长篇目录", intro="简介" * 300, brief="创作原案" * 300)
    volumes = [
        models.Volume(
            id=f"volume-{index:02}",
            book=book,
            title=f"卷{index:02}",
            order_index=max(0, index - 1),
            summary="卷规划" * 200,
            memory_summary="卷记忆" * 100,
        )
        for index in range(40)
    ]
    chapters = [
        models.Chapter(
            id=f"chapter-{index:04}",
            book=book,
            volume=volumes[index // 20] if index < 780 else None,
            title=f"章{index:04}",
            order_index=0 if index < 2 else index,
            outline="章纲" * 300,
        )
        for index in range(800)
    ]
    # Insert tied rows in reverse ID order so a missing tie-break is observable.
    volumes[0].id, volumes[1].id = volumes[1].id, volumes[0].id
    chapters[0].id, chapters[1].id = chapters[1].id, chapters[0].id
    db.add(book)
    db.flush()
    return book.id, volumes, chapters


async def test_tree_800_chapters_projects_catalog_fields_and_matches_paged_order(
    storage: sessionmaker[Session], catalog_client: httpx.AsyncClient
) -> None:
    with storage() as db:
        book_id, volumes, chapters = seed_catalog(db)
        volumes[0].chapters[0].content = models.ChapterContent(body="正文" * 300)
        expected = [chapters[1].id, chapters[0].id]
        db.commit()
    engine = storage.kw["bind"]
    assert isinstance(engine, Engine)
    statements: list[str] = []

    def trace(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", trace)
    try:
        response = await catalog_client.get(f"/books/{book_id}/tree")
    finally:
        event.remove(engine, "before_cursor_execute", trace)
    assert response.status_code == 200
    assert len(statements) == 3
    sql = "\n".join(statements)
    assert all(
        unused not in sql
        for unused in (
            "books.intro",
            "books.brief",
            "volumes.summary",
            "volumes.memory_summary",
            "chapters.outline",
            "chapter_content",
        )
    )
    tree = response.json()
    assert len(tree["volumes"]) == 40
    assert [v["id"] for v in tree["volumes"][:2]] == ["volume-00", "volume-01"]
    assert [c["id"] for c in tree["volumes"][1]["chapters"][:2]] == expected
    assert len(tree["loose_chapters"]) == 20
    assert sum(len(v["chapters"]) for v in tree["volumes"]) == 780
    for offset, chapter_id in enumerate(expected):
        page = await catalog_client.get(
            f"/books/{book_id}/chapters", params={"offset": offset, "limit": 1}
        )
        assert page.status_code == 200 and page.json()[0]["id"] == chapter_id


@pytest.mark.parametrize("action", ["delete", "move"])
async def test_tree_uses_one_snapshot_while_another_connection_changes_volume(
    action: str, storage: sessionmaker[Session], catalog_client: httpx.AsyncClient
) -> None:
    engine = storage.kw["bind"]
    assert isinstance(engine, Engine)
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one() == "wal"
        )
    with storage() as db:
        book = models.Book(title="并发目录")
        volume = models.Volume(book=book, title="原卷")
        chapter = models.Chapter(book=book, volume=volume, title="原章")
        db.add(book)
        db.flush()
        book_id, volume_id, chapter_id = book.id, volume.id, chapter.id
        db.commit()

    changed = False

    def concurrent_change(
        _conn: object, _cursor: object, statement: str, *_args: object
    ) -> None:
        nonlocal changed
        if (
            changed
            or not statement.startswith("SELECT")
            or "FROM volumes" not in statement
        ):
            return
        changed = True
        with storage() as writer:
            if action == "delete":
                volume = writer.get(models.Volume, volume_id)
                assert volume is not None
                writer.delete(volume)
            else:
                chapter = writer.get(models.Chapter, chapter_id)
                assert chapter is not None
                chapter.volume = models.Volume(book_id=book_id, title="新卷")
            writer.commit()

    event.listen(engine, "after_cursor_execute", concurrent_change)
    try:
        response = await catalog_client.get(f"/books/{book_id}/tree")
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_change)
    assert changed and response.status_code == 200
    prior = response.json()
    assert prior["loose_chapters"] == []
    assert len(prior["volumes"]) == 1
    assert prior["volumes"][0]["id"] == volume_id
    assert [c["id"] for c in prior["volumes"][0]["chapters"]] == [chapter_id]

    current = (await catalog_client.get(f"/books/{book_id}/tree")).json()
    if action == "delete":
        assert current["volumes"] == []
        assert [c["id"] for c in current["loose_chapters"]] == [chapter_id]
    else:
        assert current["loose_chapters"] == []
        assert [v["title"] for v in current["volumes"] if v["chapters"]] == ["新卷"]


async def test_reorder_800_chapters_validates_in_batches_and_preserves_text(
    storage: sessionmaker[Session], catalog_client: httpx.AsyncClient
) -> None:
    with storage() as db:
        book_id, volumes, chapters = seed_catalog(db)
        first = chapters[0]
        first.content = models.ChapterContent(body="正文保持原样")
        items = [
            {"id": chapter.id, "volume_id": chapter.volume_id, "order_index": order}
            for order, chapter in enumerate(chapters)
        ]
        items[0].update(volume_id=volumes[1].id, order_index=800)
        first_id, target_volume_id = first.id, volumes[1].id
        db.commit()
    engine = storage.kw["bind"]
    assert isinstance(engine, Engine)
    statements: list[str] = []

    def trace(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", trace)
    try:
        response = await catalog_client.post("/chapters/reorder", json={"items": items})
    finally:
        event.remove(engine, "before_cursor_execute", trace)
    assert response.status_code == 200
    assert len(statements) == 3  # Two batches of chapters and one of distinct volumes.
    sql = "\n".join(statements)
    assert "chapters.outline" not in sql and "volumes.summary" not in sql
    with storage() as db:
        chapter = db.get(models.Chapter, first_id)
        assert chapter is not None and chapter.book_id == book_id
        assert (chapter.volume_id, chapter.order_index) == (target_volume_id, 800)
        assert chapter.content is not None and chapter.content.body == "正文保持原样"
        assert db.scalar(
            select(models.Volume.id).where(models.Volume.id == target_volume_id)
        )
