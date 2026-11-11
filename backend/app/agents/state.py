"""智能体决策所需的结构化书籍状态(架构文档里的 State 模块)。

把原来分散在 tools.py::tool_get_book_state 里的"临时拼字典"逻辑收敛成一个
有类型、可复用的 NovelState:回答"Agent 现在处于什么状态"这一件事,供
Context Builder、Tool 等其它模块消费,而不是各自重新查一遍库。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .. import models
from ..memory import facts
from .completion import chapter_complete_condition

CHAPTER_WINDOW_LIMIT = 32


@dataclass
class CharacterState:
    id: str
    name: str
    derived_state: dict[str, str] = field(default_factory=dict)


@dataclass
class ChapterState:
    id: str
    title: str
    order_index: int
    status: str
    complete: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "order_index": self.order_index,
            "status": self.status,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class ChapterWindow:
    offset: int
    total: int
    returned: int

    def to_dict(self) -> dict[str, object]:
        end = self.offset + self.returned
        return {
            "offset": self.offset,
            "limit": CHAPTER_WINDOW_LIMIT,
            "total": self.total,
            "next_offset": end if self.returned and end < self.total else None,
            "truncated": self.returned < self.total,
            "order": "pending_first",
        }


@dataclass
class VolumeState:
    volume_id: str
    title: str
    chapter_count: int
    chapters_done: int
    chapters: list[ChapterState]


@dataclass
class PlotThreadState:
    name: str
    status: str


@dataclass
class NovelState:
    """某本书当前的创作状态快照;只读,每次决策前重新查库生成,不缓存。"""

    title: str
    genre: str
    tone: str
    target_words: int
    worldbuilding_count: int
    characters: list[CharacterState]
    volumes: list[VolumeState]
    loose_chapters: list[ChapterState]
    loose_chapters_total: int
    loose_chapters_done: int
    final_chapter: ChapterState | None
    chapter_window: ChapterWindow
    chapters_total: int
    chapters_done: int
    total_word_count: int
    open_plot_threads: list[PlotThreadState]

    def to_dict(self) -> dict[str, object]:
        """序列化成工具观察结果 / Context 文本所需的纯 JSON 结构。"""
        return {
            "title": self.title,
            "genre": self.genre,
            "tone": self.tone,
            "target_words": self.target_words,
            "worldbuilding_count": self.worldbuilding_count,
            "characters": [
                {"id": c.id, "name": c.name, "derived_state": c.derived_state}
                for c in self.characters
            ],
            "volumes": [
                {
                    "volume_id": v.volume_id,
                    "title": v.title,
                    "chapter_count": v.chapter_count,
                    "chapters_done": v.chapters_done,
                    "chapters": [chapter.to_dict() for chapter in v.chapters],
                }
                for v in self.volumes
            ],
            "loose_chapters": [chapter.to_dict() for chapter in self.loose_chapters],
            "loose_chapters_total": self.loose_chapters_total,
            "loose_chapters_done": self.loose_chapters_done,
            "final_chapter": self.final_chapter.to_dict()
            if self.final_chapter
            else None,
            "chapter_window": self.chapter_window.to_dict(),
            "chapters_total": self.chapters_total,
            "chapters_done": self.chapters_done,
            "total_word_count": self.total_word_count,
            "open_plot_threads": [
                {"name": t.name, "status": t.status} for t in self.open_plot_threads
            ],
        }


class BookNotFound(Exception):
    """书籍不存在;由调用方决定转换成 ToolError / HTTP 404 等具体表现形式。"""


def load_state(db: Session, book_id: str, *, chapter_offset: int = 0) -> NovelState:
    if chapter_offset < 0:
        raise ValueError("chapter_offset must be non-negative")
    book = db.get(models.Book, book_id)
    if not book:
        raise BookNotFound(book_id)

    wb_count = (
        db.scalar(
            select(func.count())
            .select_from(models.Worldbuilding)
            .where(models.Worldbuilding.book_id == book_id)
        )
        or 0
    )
    characters = db.execute(
        select(models.Character.id, models.Character.name)
        .where(models.Character.book_id == book_id)
        .order_by(models.Character.order_index, models.Character.id)
    ).all()
    volumes = db.execute(
        select(models.Volume.id, models.Volume.title)
        .where(models.Volume.book_id == book_id)
        .order_by(models.Volume.order_index, models.Volume.id)
    ).all()
    volume_ids = {volume_id for volume_id, _ in volumes}

    complete = chapter_complete_condition()
    counts_by_volume: dict[str, tuple[int, int]] = {}
    loose_total = loose_done = total = done = word_count = 0
    for volume_id, chapter_count, completed, words in db.execute(
        select(
            models.Chapter.volume_id,
            func.count(models.Chapter.id),
            func.count(case((complete, 1))),
            func.coalesce(func.sum(models.Chapter.word_count), 0),
        )
        .outerjoin(models.ChapterContent)
        .where(models.Chapter.book_id == book_id)
        .group_by(models.Chapter.volume_id)
    ):
        total += chapter_count
        done += completed
        word_count += words
        if volume_id is not None and volume_id in volume_ids:
            counts_by_volume[volume_id] = (chapter_count, completed)
        else:
            loose_total += chapter_count
            loose_done += completed

    chapter_metadata = (
        select(
            models.Chapter.id,
            models.Chapter.volume_id,
            models.Chapter.title,
            models.Chapter.order_index,
            models.Chapter.status,
            complete,
        )
        .outerjoin(models.ChapterContent)
        .where(models.Chapter.book_id == book_id)
    )
    offset = min(chapter_offset, total)
    selected = db.execute(
        chapter_metadata.order_by(
            complete.asc(),
            models.Chapter.order_index,
            models.Chapter.id,
        )
        .limit(CHAPTER_WINDOW_LIMIT)
        .offset(offset)
    ).all()
    chapters_by_volume: defaultdict[str | None, list[ChapterState]] = defaultdict(list)
    for chapter_id, volume_id, title, order_index, status, is_complete in selected:
        group = volume_id if volume_id in volume_ids else None
        chapters_by_volume[group].append(
            ChapterState(chapter_id, title, order_index, status, is_complete)
        )
    for chapters in chapters_by_volume.values():
        chapters.sort(key=lambda chapter: (chapter.order_index, chapter.id))

    volume_states = []
    for volume_id, volume_title in volumes:
        volume_total, volume_done = counts_by_volume.get(volume_id, (0, 0))
        volume_states.append(
            VolumeState(
                volume_id=volume_id,
                title=volume_title,
                chapter_count=volume_total,
                chapters_done=volume_done,
                chapters=chapters_by_volume[volume_id],
            )
        )

    final = db.execute(
        chapter_metadata.order_by(
            models.Chapter.order_index.desc(),
            models.Chapter.id.desc(),
        ).limit(1)
    ).first()
    final_chapter: ChapterState | None = None
    if final is not None:
        chapter_id, _, title, order_index, status, is_complete = final
        final_chapter = ChapterState(
            chapter_id, title, order_index, status, is_complete
        )

    threads = db.execute(
        select(
            models.PlotThread.id, models.PlotThread.name, models.PlotThread.status
        ).where(
            models.PlotThread.book_id == book_id,
        )
    ).all()
    derived = facts.replay(db, book_id)

    return NovelState(
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
        worldbuilding_count=wb_count,
        characters=[
            CharacterState(
                id=character_id,
                name=name,
                derived_state=derived.characters.get(character_id, {}),
            )
            for character_id, name in characters
        ],
        volumes=volume_states,
        loose_chapters=chapters_by_volume[None],
        loose_chapters_total=loose_total,
        loose_chapters_done=loose_done,
        final_chapter=final_chapter,
        chapter_window=ChapterWindow(
            offset=offset, total=total, returned=len(selected)
        ),
        chapters_total=total,
        chapters_done=done,
        total_word_count=word_count,
        open_plot_threads=[
            PlotThreadState(name=name, status=status)
            for thread_id, name, authored_status in threads
            if (status := derived.threads.get(thread_id) or authored_status) != "closed"
        ],
    )
