"""临时 SQLite 和独立 FastAPI 实例覆盖章节归属、批量原子性与手工清空正文。"""

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.db_base import Base


@pytest.fixture
def api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Session]]:
    # 导入路由前切换目录,配置只使用临时数据库,不读取项目 .env。
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from app.api import chapters, export
    from app.database import get_db

    engine = create_engine(
        f"sqlite:///{tmp_path}/chapters.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        app = FastAPI()
        app.include_router(chapters.router)
        app.include_router(export.router)

        def session_override() -> Iterator[Session]:
            yield db

        app.dependency_overrides[get_db] = session_override
        with TestClient(app) as client:
            yield client, db
    engine.dispose()


def seed(db: Session) -> tuple[models.Chapter, models.Volume, models.Volume]:
    book = models.Book(title="本书")
    volume = models.Volume(book=book, title="本书卷")
    chapter = models.Chapter(
        book=book, volume=volume, title="已有章", order_index=3
    )
    other = models.Volume(book=models.Book(title="他书"), title="他书卷")
    db.add_all([book, other])
    db.commit()
    return chapter, volume, other


@pytest.mark.parametrize("invalid_volume", ["missing", "foreign"])
def test_create_rejects_invalid_volume(
    api: tuple[TestClient, Session], invalid_volume: str
) -> None:
    client, db = api
    chapter, _, other = seed(db)
    response = client.post(
        "/chapters",
        json={
            "book_id": chapter.book_id,
            "volume_id": other.id if invalid_volume == "foreign" else "missing",
            "title": "非法新章",
        },
    )
    assert response.status_code == (400 if invalid_volume == "foreign" else 404)
    db.commit()
    assert len(db.scalars(select(models.Chapter)).all()) == 1


@pytest.mark.parametrize("invalid_volume", ["missing", "foreign"])
def test_update_validates_before_changing_any_fields(
    api: tuple[TestClient, Session], invalid_volume: str
) -> None:
    client, db = api
    chapter, volume, other = seed(db)
    response = client.patch(
        f"/chapters/{chapter.id}",
        json={
            "title": "不应持久化",
            "volume_id": other.id if invalid_volume == "foreign" else "missing",
        },
    )
    assert response.status_code == (400 if invalid_volume == "foreign" else 404)
    assert not db.dirty
    db.commit()
    db.refresh(chapter)
    assert (chapter.title, chapter.volume_id) == ("已有章", volume.id)


def test_create_update_and_reorder_accept_same_book_and_unassigned(
    api: tuple[TestClient, Session],
) -> None:
    client, db = api
    chapter, volume, _ = seed(db)
    created = client.post(
        "/chapters",
        json={"book_id": chapter.book_id, "volume_id": volume.id, "title": "新章"},
    )
    assert created.status_code == 200
    chapter_id = created.json()["id"]
    assert client.patch(f"/chapters/{chapter_id}", json={"title": "重命名"}).json()[
        "volume_id"
    ] == volume.id
    assert client.patch(
        f"/chapters/{chapter_id}", json={"volume_id": None}
    ).json()["volume_id"] is None
    response = client.post(
        "/chapters/reorder",
        json={
            "items": [
                {"id": chapter.id, "volume_id": None, "order_index": 1},
                {"id": chapter_id, "volume_id": volume.id, "order_index": 0},
            ]
        },
    )
    assert response.status_code == 200
    db.expire_all()
    saved = db.get(models.Chapter, chapter_id)
    assert saved is not None
    assert (saved.volume_id, saved.order_index) == (volume.id, 0)
    assert (chapter.volume_id, chapter.order_index) == (None, 1)


@pytest.mark.parametrize(
    ("invalid_item", "expected_status"),
    [("missing-chapter", 404), ("missing-volume", 404), ("foreign-volume", 400),
     ("foreign-chapter", 400), ("duplicate", 400)],
)
def test_reorder_rejects_entire_batch_without_pending_or_persisted_writes(
    api: tuple[TestClient, Session], invalid_item: str, expected_status: int
) -> None:
    client, db = api
    chapter, volume, other = seed(db)
    second = models.Chapter(book_id=chapter.book_id, title="第二章", order_index=4)
    foreign = models.Chapter(book_id=other.book_id, title="他书章节", order_index=8)
    db.add_all([second, foreign])
    db.commit()
    items = [
        {"id": chapter.id, "volume_id": None, "order_index": 99},
        {"id": second.id, "volume_id": volume.id, "order_index": 100},
    ]
    if invalid_item == "missing-chapter":
        items[1]["id"] = "missing"
    elif invalid_item == "missing-volume":
        items[1]["volume_id"] = "missing"
    elif invalid_item == "foreign-volume":
        items[1]["volume_id"] = other.id
    elif invalid_item == "foreign-chapter":
        items[1]["id"] = foreign.id
        items[1]["volume_id"] = other.id
    else:
        items[1]["id"] = chapter.id

    response = client.post("/chapters/reorder", json={"items": items})
    assert response.status_code == expected_status
    assert not db.dirty
    # 保留同一 Session 并主动 commit,证明校验失败前没有 autoflush 或待提交改动。
    db.commit()
    db.expire_all()
    assert (chapter.volume_id, chapter.order_index) == (volume.id, 3)
    assert (second.volume_id, second.order_index) == (None, 4)
    assert (foreign.volume_id, foreign.order_index) == (None, 8)


@pytest.mark.parametrize("format_", ["txt", "epub"])
def test_export_excludes_legacy_cross_book_volume_association(
    api: tuple[TestClient, Session], format_: str
) -> None:
    client, db = api
    chapter, volume, other = seed(db)
    chapter.content = models.ChapterContent(body="本书正文")
    leaked = models.Chapter(
        book_id=other.book_id, volume_id=volume.id, title="外书私密章",
        content=models.ChapterContent(body="不可泄漏的外书正文"),
    )
    db.add(leaked)
    db.commit()
    response = client.get(f"/books/{chapter.book_id}/export?format={format_}")
    assert response.status_code == 200
    if format_ == "epub":
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            content = "\n".join(
                archive.read(name).decode("utf-8") for name in archive.namelist()
            )
    else:
        content = response.text
    assert "本书正文" in content
    assert "外书私密章" not in content
    assert "不可泄漏的外书正文" not in content


@pytest.mark.parametrize("outline", ["", " \u3000\n", "已有章纲"])
@pytest.mark.parametrize("body", ["", " \r\n\t\u3000\u00a0\u2003"])
def test_manual_blank_content_resets_metadata_and_completion(
    api: tuple[TestClient, Session], outline: str, body: str
) -> None:
    from app.agents.completion import chapter_counts

    client, db = api
    chapter, _, _ = seed(db)
    chapter.outline = outline
    chapter.status = "done"
    chapter.word_count = 7
    chapter.content = models.ChapterContent(body="以前完成的正文")
    db.commit()
    response = client.put(f"/chapters/{chapter.id}/content", json={"body": body})
    assert response.status_code == 200
    assert response.json()["body"] == body
    metadata = client.get(f"/chapters/{chapter.id}").json()
    assert metadata["status"] == ("outline" if outline.strip() else "empty")
    assert metadata["word_count"] == 0
    db.expire_all()
    assert chapter_counts(db, chapter.book_id) == (1, 0)
    saved_content = db.get(models.ChapterContent, chapter.id)
    assert saved_content is not None
    assert saved_content.body == body


@pytest.mark.parametrize("existing_content", [False, True])
def test_nonblank_manual_save_preserves_body_and_marks_done(
    api: tuple[TestClient, Session], existing_content: bool
) -> None:
    from app.agents.completion import chapter_counts

    client, db = api
    chapter, _, _ = seed(db)
    if existing_content:
        chapter.content = models.ChapterContent(body="旧正文")
        db.commit()
    body = " \n新正文\u3000"
    response = client.put(f"/chapters/{chapter.id}/content", json={"body": body})
    assert response.status_code == 200
    db.expire_all()
    assert chapter.status == "done"
    assert chapter.word_count == len(body)
    assert chapter.content is not None
    assert chapter.content.body == body
    assert chapter_counts(db, chapter.book_id) == (1, 1)
