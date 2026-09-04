"""智能体决策所需的结构化书籍状态(架构文档里的 State 模块)。

把原来分散在 tools.py::tool_get_book_state 里的"临时拼字典"逻辑收敛成一个
有类型、可复用的 NovelState:回答"Agent 现在处于什么状态"这一件事,供
Context Builder、Tool 等其它模块消费,而不是各自重新查一遍库。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models


@dataclass
class CharacterState:
    id: str
    name: str


@dataclass
class ChapterState:
    id: str
    title: str
    order_index: int
    status: str


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
            "characters": [{"id": c.id, "name": c.name} for c in self.characters],
            "volumes": [
                {
                    "volume_id": v.volume_id,
                    "title": v.title,
                    "chapter_count": v.chapter_count,
                    "chapters_done": v.chapters_done,
                    "chapters": [
                        {
                            "id": c.id,
                            "title": c.title,
                            "order_index": c.order_index,
                            "status": c.status,
                        }
                        for c in v.chapters
                    ],
                }
                for v in self.volumes
            ],
            "chapters_total": self.chapters_total,
            "chapters_done": self.chapters_done,
            "total_word_count": self.total_word_count,
            "open_plot_threads": [
                {"name": t.name, "status": t.status} for t in self.open_plot_threads
            ],
        }


class BookNotFound(Exception):
    """书籍不存在;由调用方决定转换成 ToolError / HTTP 404 等具体表现形式。"""


def load_state(db: Session, book_id: str) -> NovelState:
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
    characters = db.scalars(
        select(models.Character)
        .where(models.Character.book_id == book_id)
        .order_by(models.Character.order_index)
    ).all()
    volumes = db.scalars(
        select(models.Volume)
        .where(models.Volume.book_id == book_id)
        .order_by(models.Volume.order_index)
    ).all()
    all_chapters = db.scalars(
        select(models.Chapter).where(models.Chapter.book_id == book_id)
    ).all()

    volume_states = []
    for v in volumes:
        chapters = sorted(
            (c for c in all_chapters if c.volume_id == v.id),
            key=lambda c: c.order_index,
        )
        volume_states.append(
            VolumeState(
                volume_id=v.id,
                title=v.title,
                chapter_count=len(chapters),
                chapters_done=sum(1 for c in chapters if c.status == "done"),
                chapters=[
                    ChapterState(
                        id=c.id,
                        title=c.title,
                        order_index=c.order_index,
                        status=c.status,
                    )
                    for c in chapters
                ],
            )
        )

    threads = db.scalars(
        select(models.PlotThread).where(
            models.PlotThread.book_id == book_id,
            models.PlotThread.status != "closed",
        )
    ).all()

    return NovelState(
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
        worldbuilding_count=wb_count,
        characters=[CharacterState(id=c.id, name=c.name) for c in characters],
        volumes=volume_states,
        chapters_total=len(all_chapters),
        chapters_done=sum(1 for c in all_chapters if c.status == "done"),
        total_word_count=sum(c.word_count for c in all_chapters),
        open_plot_threads=[
            PlotThreadState(name=t.name, status=t.status) for t in threads
        ],
    )
