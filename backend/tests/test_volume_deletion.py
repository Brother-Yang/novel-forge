"""删除卷只解除章节归卷;书籍仍然拥有章节及其正文、摘要。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from app import models
from app.db_base import Base
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.fixture(params=[False, True], ids=["foreign-keys-off", "foreign-keys-on"])
def db(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Session]:
    # 路由导入会初始化配置;使用临时目录和内存库,不读取项目 .env。
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text(f"PRAGMA foreign_keys = {int(request.param)}"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _chapter(book: models.Book, volume: models.Volume | None) -> models.Chapter:
    return models.Chapter(
        book=book,
        volume=volume,
        title="已有章节",
        outline="保留大纲",
        order_index=7,
        status="done",
        word_count=1234,
        revision_approved=True,
        content=models.ChapterContent(body="不能丢失的章节正文"),
        summary=models.ChapterSummary(summary="不能丢失的章节摘要"),
    )


@pytest.mark.parametrize("loaded", [False, True], ids=["unloaded", "loaded"])
@pytest.mark.parametrize("use_api", [False, True], ids=["orm", "api"])
def test_delete_volume_preserves_chapters(
    db: Session, loaded: bool, use_api: bool
) -> None:
    from app.api.volumes import delete_volume

    book = models.Book(title="测试书籍")
    removed_volume = models.Volume(book=book, title="待删除卷")
    retained_volume = models.Volume(book=book, title="保留卷")
    chapter = _chapter(book, removed_volume)
    second_chapter = _chapter(book, removed_volume)
    retained_chapter = _chapter(book, retained_volume)
    loose_chapter = _chapter(book, None)
    other_book = models.Book(title="其他书籍")
    other_volume = models.Volume(book=other_book, title="其他书籍的卷")
    other_chapter = _chapter(other_book, other_volume)
    db.add_all([book, other_book])
    db.commit()
    book_id, removed_id, retained_id = book.id, removed_volume.id, retained_volume.id
    chapter_ids = [chapter.id, second_chapter.id]
    unaffected = {
        retained_chapter.id: retained_id,
        loose_chapter.id: None,
        other_chapter.id: other_volume.id,
    }
    db.expunge_all()

    volume = db.get(models.Volume, removed_id)
    assert volume is not None
    if loaded:
        assert len(volume.chapters) == 2
        for item in volume.chapters:
            assert item.content is not None
            assert item.summary is not None

    if use_api:
        assert delete_volume(removed_id, db) == {"ok": True}
    else:
        db.delete(volume)
        db.commit()
    db.expunge_all()

    assert db.get(models.Volume, removed_id) is None
    assert db.get(models.Volume, retained_id) is not None
    for chapter_id in chapter_ids:
        saved = db.get(models.Chapter, chapter_id)
        assert saved is not None
        assert saved.book_id == book_id
        assert saved.volume_id is None
        assert saved.title == "已有章节"
        assert saved.outline == "保留大纲"
        assert saved.order_index == 7
        assert saved.status == "done"
        assert saved.word_count == 1234
        assert saved.revision_approved is True
        assert saved.content is not None
        assert saved.content.body == "不能丢失的章节正文"
        assert saved.summary is not None
        assert saved.summary.summary == "不能丢失的章节摘要"
    for chapter_id, volume_id in unaffected.items():
        saved = db.get(models.Chapter, chapter_id)
        assert saved is not None
        assert saved.volume_id == volume_id
        assert saved.content is not None
        assert saved.summary is not None


@pytest.mark.parametrize("loaded", [False, True], ids=["unloaded", "loaded"])
def test_delete_book_still_deletes_its_chapters(db: Session, loaded: bool) -> None:
    from app.api.books import delete_book
    from app.api.volumes import delete_volume

    book = models.Book(title="待删除书籍")
    volume = models.Volume(book=book, title="卷")
    detached_volume = models.Volume(book=book, title="先删除的卷")
    chapters = [
        _chapter(book, volume),
        _chapter(book, detached_volume),
        _chapter(book, None),
    ]
    other_book = models.Book(title="保留书籍")
    other_chapter = _chapter(other_book, None)
    db.add_all([book, other_book])
    db.commit()
    book_id, detached_id = book.id, detached_volume.id
    chapter_ids = [chapter.id for chapter in chapters]
    other_id = other_chapter.id
    delete_volume(detached_id, db)
    db.expunge_all()

    saved_book = db.get(models.Book, book_id)
    assert saved_book is not None
    if loaded:
        assert len(saved_book.chapters) == 3
        assert len(saved_book.volumes) == 1
        assert len(saved_book.volumes[0].chapters) == 1
    assert delete_book(book_id, db) == {"ok": True}
    db.expunge_all()

    assert db.get(models.Book, book_id) is None
    assert (
        db.scalars(select(models.Volume).where(models.Volume.book_id == book_id)).all()
        == []
    )
    for chapter_id in chapter_ids:
        assert db.get(models.Chapter, chapter_id) is None
        assert db.get(models.ChapterContent, chapter_id) is None
        assert db.get(models.ChapterSummary, chapter_id) is None
    assert db.get(models.Chapter, other_id) is not None
    assert db.get(models.ChapterContent, other_id) is not None
    assert db.get(models.ChapterSummary, other_id) is not None


def test_delete_unknown_volume_returns_404_without_changes(db: Session) -> None:
    from app.api.volumes import delete_volume

    book = models.Book(title="书籍")
    volume = models.Volume(book=book, title="保留卷")
    chapter = _chapter(book, volume)
    db.add(book)
    db.commit()
    volume_id, chapter_id = volume.id, chapter.id

    with pytest.raises(HTTPException) as error:
        delete_volume("missing-volume", db)

    assert error.value.status_code == 404
    assert error.value.detail == "卷不存在"
    assert db.get(models.Volume, volume_id) is not None
    saved = db.get(models.Chapter, chapter_id)
    assert saved is not None
    assert saved.volume_id == volume_id
    assert saved.content is not None
    assert saved.summary is not None
