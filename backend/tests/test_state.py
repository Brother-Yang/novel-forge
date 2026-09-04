"""State 模块测试:load_state 对书籍当前创作状态的聚合是否正确
(设定/人物/卷/章/字数/剧情线程),以及 to_dict() 序列化后的结构。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.agents.state import BookNotFound, load_state
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
    db.add(
        models.Chapter(
            book_id=book.id,
            volume_id=volume.id,
            title="第1章",
            status="done",
            word_count=1200,
        )
    )
    db.add(
        models.Chapter(
            book_id=book.id,
            volume_id=volume.id,
            title="第2章",
            status="outline",
            word_count=0,
        )
    )
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
        "chapters_total",
        "chapters_done",
        "total_word_count",
        "open_plot_threads",
    }
    assert d["characters"] == []
    assert d["volumes"] == []
    assert d["open_plot_threads"] == []
