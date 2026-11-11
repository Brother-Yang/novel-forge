import io
import math
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from starlette.types import Message, Scope

from app import models
from app.api import export
from app.database import get_db
from app.db_base import Base


def _export_app(engine: Engine) -> FastAPI:
    def session() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(export.router)
    app.dependency_overrides[get_db] = session
    return app


@pytest.fixture
def manuscript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, str, Path]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'manuscript.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = models.Book(title="书稿 <新>\x00")
        db.add(
            models.Chapter(
                book=book,
                title="开篇 & 场景\x08",
                content=models.ChapterContent(body="照常保留 <tag>\x0b 🌙\n下一段"),
            )
        )
        db.flush()
        book_id = book.id
        db.commit()

    files = tmp_path / "exports"
    files.mkdir()
    monkeypatch.setattr(export.tempfile, "tempdir", str(files))
    try:
        yield _export_app(engine), f"/books/{book_id}/export?format=epub", files
    finally:
        engine.dispose()


async def test_epub_escapes_markup_and_replaces_invalid_xml_characters(
    manuscript: tuple[FastAPI, str, Path],
) -> None:
    app, url, files = manuscript
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(url)

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        chapter = ElementTree.fromstring(archive.read("OEBPS/chap1.xhtml"))
        assert "开篇 & 场景\ufffd" in list(chapter.itertext())
        assert "照常保留 <tag>\ufffd 🌙" in list(chapter.itertext())
        assert "下一段" in list(chapter.itertext())
        for name in ("OEBPS/content.opf", "OEBPS/toc.ncx"):
            document = ElementTree.fromstring(archive.read(name))
            assert "书稿 <新>\ufffd" in list(document.itertext())
    assert list(files.iterdir()) == []


async def test_epub_cleans_up_after_render_failure(
    manuscript: tuple[FastAPI, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, url, files = manuscript

    def fail_to_render(title: str, body: str) -> str:
        assert list(files.glob("*/book.epub"))
        raise OSError("写入失败")

    monkeypatch.setattr(export, "_xhtml", fail_to_render)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(url)
    assert response.status_code == 500
    assert list(files.iterdir()) == []


@pytest.mark.parametrize(
    ("range_header", "expected_status"), [("bytes=bad", 400), ("bytes=1000000-", 416)]
)
async def test_epub_cleans_up_when_range_is_rejected(
    manuscript: tuple[FastAPI, str, Path], range_header: str, expected_status: int
) -> None:
    app, url, files = manuscript
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(url, headers={"Range": range_header})
    assert response.status_code == expected_status
    assert list(files.iterdir()) == []


async def test_epub_cleans_up_when_send_fails(
    manuscript: tuple[FastAPI, str, Path],
) -> None:
    app, url, files = manuscript
    path, query = url.split("?", maxsplit=1)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": [],
    }

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            assert list(files.glob("*/book.epub"))
            raise ConnectionError("连接已关闭")

    with pytest.raises(ConnectionError, match="连接已关闭"):
        await app(scope, receive, send)
    assert list(files.iterdir()) == []


@pytest.mark.parametrize("format_", ["txt", "epub"])
async def test_export_uses_one_snapshot_during_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, format_: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshot.db'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = models.Book(title="原书名")
        first = models.Chapter(
            book=book,
            title="第一章",
            order_index=0,
            content=models.ChapterContent(body="原开篇"),
        )
        second = models.Chapter(
            book=book,
            title="原次章标题",
            order_index=1,
            content=models.ChapterContent(body="原次章正文"),
        )
        db.add_all([first, second])
        db.flush()
        book_id, first_id, second_id = book.id, first.id, second.id
        db.commit()

    monkeypatch.setattr(export, "_CHAPTER_BATCH_SIZE", 1)
    original_bodies = export._chapter_bodies
    saved = False

    def read_bodies(db: Session, chapter_ids: Sequence[str]) -> dict[str, str]:
        nonlocal saved
        if first_id in chapter_ids:
            with Session(engine) as editor:
                chapter = editor.get(models.Chapter, second_id)
                assert chapter is not None and chapter.content is not None
                chapter.title = "新次章标题"
                chapter.content.body = "新次章正文"
                editor.commit()
            saved = True
        return original_bodies(db, chapter_ids)

    monkeypatch.setattr(export, "_chapter_bodies", read_bodies)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_export_app(engine)), base_url="http://test"
        ) as client:
            response = await client.get(f"/books/{book_id}/export?format={format_}")
        assert saved and response.status_code == 200
        if format_ == "epub":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                content = archive.read("OEBPS/chap2.xhtml").decode()
        else:
            content = response.text
        assert "原次章标题" in content and "原次章正文" in content
        assert "新次章标题" not in content and "新次章正文" not in content
        with Session(engine) as editor:
            chapter = editor.get(models.Chapter, second_id)
            assert chapter is not None and chapter.content is not None
            assert (
                chapter.title == "新次章标题" and chapter.content.body == "新次章正文"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("format_", ["txt", "epub"])
async def test_large_export_batches_bodies_and_orders_tied_chapters(
    tmp_path: Path, format_: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'large-export.db'}")
    Base.metadata.create_all(engine)
    expected_titles = [
        f"章-{volume_number:02d}-{chapter_number:02d}"
        for volume_number in range(40)
        for chapter_number in range(20)
    ] + ["章-未归卷-A", "章-未归卷-Z"]
    with Session(engine) as db:
        book = models.Book(id="large-book", title="长篇书稿")
        # Insert higher IDs first to ensure ties cannot rely on insertion order.
        for volume_number in reversed(range(40)):
            volume = models.Volume(
                id=f"volume-{volume_number:02d}",
                book=book,
                title="同名卷" if volume_number < 2 else f"第{volume_number + 1}卷",
                order_index=volume_number // 2,
            )
            for chapter_number in reversed(range(20)):
                chapter_key = f"{volume_number:02d}-{chapter_number:02d}"
                db.add(
                    models.Chapter(
                        id=f"chapter-{chapter_key}",
                        book=book,
                        volume=volume,
                        title=f"章-{chapter_key}",
                        order_index=chapter_number // 2,
                        content=models.ChapterContent(body=f"正文-{chapter_key}"),
                    )
                )
        for name in ("Z", "A"):
            db.add(
                models.Chapter(
                    id=f"loose-{name}",
                    book=book,
                    title=f"章-未归卷-{name}",
                    order_index=0,
                )
            )
        db.commit()

    selects: list[str] = []
    body_batches: list[int] = []

    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("SELECT"):
            selects.append(statement)
            if "FROM chapter_content" in statement:
                assert isinstance(parameters, tuple)
                body_batches.append(len(parameters))

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_export_app(engine)), base_url="http://test"
        ) as client:
            response = await client.get(f"/books/large-book/export?format={format_}")
        assert response.status_code == 200
        if format_ == "txt":
            lines = response.text.splitlines()
            assert [line for line in lines if line.startswith("章-")] == expected_titles
            assert lines.count("同名卷") == 2
            assert "正文-00-00" in lines and "正文-39-19" in lines
        else:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                namespace = {"x": "http://www.w3.org/1999/xhtml"}
                chapter_names = [
                    name for name in archive.namelist() if name.endswith(".xhtml")
                ]
                titles = [
                    ElementTree.fromstring(archive.read(name)).findtext(
                        ".//x:h2", namespaces=namespace
                    )
                    for name in chapter_names
                ]
                assert titles == expected_titles
                assert "正文-00-00" in archive.read(chapter_names[0]).decode()
                assert "正文-39-19" in archive.read(chapter_names[-3]).decode()
        assert 0 < max(body_batches) <= export._CHAPTER_BATCH_SIZE
        assert sum(body_batches) == len(expected_titles)
        assert len(selects) <= 2 + math.ceil(
            len(expected_titles) / export._CHAPTER_BATCH_SIZE
        )
    finally:
        engine.dispose()
