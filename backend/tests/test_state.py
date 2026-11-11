"""State 模块测试:load_state 对书籍当前创作状态的聚合是否正确
(设定/人物/卷/章/字数/剧情线程),以及 to_dict() 序列化后的结构。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import models
from app.agents.completion import (
    chapter_counts,
    chapter_is_complete,
    require_book_complete,
)
from app.agents.state import CHAPTER_WINDOW_LIMIT, BookNotFound, NovelState, load_state
from app.db_base import Base


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/state_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _book(db: Session, **kwargs: object) -> models.Book:
    book = models.Book(
        title="测试之书", genre="玄幻", tone="热血", target_words=10000, **kwargs
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def test_load_state_missing_book_raises(db):
    with pytest.raises(BookNotFound):
        load_state(db, "no-such-book")


def test_load_state_aggregates_full_picture(db):
    book = _book(db)
    db.add(models.Worldbuilding(book_id=book.id, title="设定1", md_content="x"))
    db.add(models.Character(book_id=book.id, name="主角", order_index=0))
    volume = models.Volume(book_id=book.id, title="第一卷", order_index=0)
    db.add(volume)
    db.commit()
    db.refresh(volume)
    chapter1 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第1章",
        status="done",
        word_count=1200,
        order_index=0,
        content=models.ChapterContent(body="已完成的正文"),
    )
    chapter2 = models.Chapter(
        book_id=book.id,
        volume_id=volume.id,
        title="第2章",
        status="outline",
        word_count=0,
        order_index=1,
    )
    db.add(chapter1)
    db.add(chapter2)
    db.add(models.PlotThread(book_id=book.id, name="线程A", status="open"))
    db.add(models.PlotThread(book_id=book.id, name="线程B", status="closed"))
    db.commit()

    state = load_state(db, book.id)

    assert state.title == book.title
    assert state.genre == "玄幻"
    assert state.tone == "热血"
    assert state.target_words == 10000
    assert state.worldbuilding_count == 1
    assert [c.name for c in state.characters] == ["主角"]
    assert state.chapters_total == 2
    assert state.chapters_done == 1
    assert state.total_word_count == 1200
    assert len(state.volumes) == 1
    assert state.volumes[0].chapter_count == 2
    assert state.volumes[0].chapters_done == 1
    # 章节须携带真实 id,供 Agent 调用 write_chapter 时引用,不能靠猜测
    assert [c.id for c in state.volumes[0].chapters] == [chapter1.id, chapter2.id]
    assert [c.status for c in state.volumes[0].chapters] == ["done", "outline"]
    assert [c.complete for c in state.volumes[0].chapters] == [True, False]
    # 已关闭的剧情线程不应出现在 open_plot_threads
    assert [t.name for t in state.open_plot_threads] == ["线程A"]


def test_load_state_to_dict_matches_shape(db):
    book = _book(db)
    state = load_state(db, book.id)
    d = state.to_dict()

    assert set(d.keys()) == {
        "title",
        "genre",
        "tone",
        "target_words",
        "worldbuilding_count",
        "characters",
        "volumes",
        "loose_chapters",
        "loose_chapters_total",
        "loose_chapters_done",
        "final_chapter",
        "chapter_window",
        "chapters_total",
        "chapters_done",
        "total_word_count",
        "open_plot_threads",
    }
    assert d["characters"] == []
    assert d["volumes"] == []
    assert d["loose_chapters"] == []
    assert d["final_chapter"] is None
    assert d["chapter_window"] == {
        "offset": 0,
        "limit": CHAPTER_WINDOW_LIMIT,
        "total": 0,
        "next_offset": None,
        "truncated": False,
        "order": "pending_first",
    }
    assert d["open_plot_threads"] == []


def test_state_counts_match_body_completion_even_for_legacy_metadata(
    db: Session,
) -> None:
    book = _book(db)
    cases = [
        ("done", None, False),
        ("done", "", False),
        ("done", " \t\n\u3000\u2003", False),
        ("done", " \0正文", True),
        ("outline", "仍需完成的正文", False),
    ]
    rows = []
    for order, (status, body, _) in enumerate(cases):
        chapter = models.Chapter(
            book_id=book.id, title=f"第{order + 1}章", order_index=order, status=status
        )
        if body is not None:
            chapter.content = models.ChapterContent(body=body)
        rows.append(chapter)
    db.add_all(rows)
    db.commit()

    snapshot = load_state(db, book.id)
    expected = [complete for _, _, complete in cases]
    assert [chapter.complete for chapter in snapshot.loose_chapters] == expected
    assert [chapter_is_complete(chapter) for chapter in rows] == expected
    assert (
        (snapshot.chapters_total, snapshot.chapters_done)
        == chapter_counts(db, book.id)
        == (5, 1)
    )
    assert (snapshot.loose_chapters_total, snapshot.loose_chapters_done) == (5, 1)


def _visible_orders(snapshot: NovelState) -> set[int]:
    return {
        chapter.order_index
        for volume in snapshot.volumes
        for chapter in volume.chapters
    } | {chapter.order_index for chapter in snapshot.loose_chapters}


def test_state_windows_800_chapters_and_keeps_deleted_volumes_chapters_accessible(
    db: Session,
) -> None:
    book = _book(db)
    volumes = [
        models.Volume(book=book, title=f"第{i + 1}卷", order_index=i) for i in range(40)
    ]
    db.add_all(volumes)
    db.flush()
    chapters = []
    for order in range(800):
        chapter = models.Chapter(
            book_id=book.id,
            volume_id=volumes[order // 20].id,
            title=f"第{order + 1}章 城门暗号",
            order_index=order,
            outline="存储细纲" * 80,
            status="done" if order < 760 else "outline",
        )
        if order < 760:
            chapter.content = models.ChapterContent(body="实际正文")
        chapters.append(chapter)
    db.add_all(chapters)
    db.commit()
    book_id, ending_id = book.id, chapters[-1].id
    db.delete(volumes[-1])
    db.commit()

    def check_projection(
        _connection: object, _cursor: object, statement: str, *_args: object
    ) -> None:
        assert "chapters.outline" not in statement, statement

    bind = db.get_bind()

    def read_state(offset: int = 0) -> NovelState:
        event.listen(bind, "before_cursor_execute", check_projection)
        try:
            return load_state(db, book_id, chapter_offset=offset)
        finally:
            event.remove(bind, "before_cursor_execute", check_projection)

    first = read_state()
    assert len(first.volumes) == 39
    assert sum(volume.chapter_count for volume in first.volumes) == 780
    assert (first.chapters_total, first.chapters_done) == (800, 760)
    assert (first.loose_chapters_total, first.loose_chapters_done) == (20, 0)
    assert first.final_chapter is not None and first.final_chapter.id == ending_id
    assert _visible_orders(first) == set(range(760, 760 + CHAPTER_WINDOW_LIMIT))
    assert len(json.dumps(first.to_dict(), ensure_ascii=False).encode()) < 20_000

    visited: set[int] = set()
    snapshot = first
    while True:
        page = _visible_orders(snapshot)
        assert 0 < len(page) <= CHAPTER_WINDOW_LIMIT and not visited.intersection(page)
        visited.update(page)
        offset = snapshot.chapter_window.to_dict()["next_offset"]
        if not isinstance(offset, int):
            break
        snapshot = read_state(offset)
    assert visited == set(range(800))

    # After writing, the default page follows the current earliest pending
    # chapter. Offsets from the previous sort order are not reused.
    first_pending = chapters[760]
    first_pending.status = "done"
    first_pending.content = models.ChapterContent(body="新写正文")
    db.commit()
    resumed = read_state()
    assert _visible_orders(resumed) == set(range(761, 761 + CHAPTER_WINDOW_LIMIT))

    for chapter in chapters[761:]:
        chapter.status = "done"
        chapter.content = models.ChapterContent(body="新写正文")
    db.commit()
    completed = read_state()
    assert completed.chapters_done == completed.chapters_total == 800
    assert completed.final_chapter is not None and completed.final_chapter.complete
    assert completed.final_chapter.id == ending_id

    require_book_complete(db, book_id)
