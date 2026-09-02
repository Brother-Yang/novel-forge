from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db_base import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    intro: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(128), default="")
    tone: Mapped[str] = mapped_column(String(128), default="")
    target_words: Mapped[int] = mapped_column(Integer, default=100000)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    volumes: Mapped[list["Volume"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    characters: Mapped[list["Character"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class Volume(Base):
    __tablename__ = "volumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")

    book: Mapped["Book"] = relationship(back_populates="volumes")
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="volume", cascade="all, delete-orphan"
    )


class Chapter(Base):
    """章节元数据,不含正文(正文在 ChapterContent,懒加载)。"""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    volume_id: Mapped[int | None] = mapped_column(
        ForeignKey("volumes.id", ondelete="SET NULL"), nullable=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(255))
    outline: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="empty")  # empty/outline/drafting/done
    word_count: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped["Book"] = relationship(back_populates="chapters")
    volume: Mapped["Volume | None"] = relationship(back_populates="chapters")
    content: Mapped["ChapterContent | None"] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", uselist=False
    )
    summary: Mapped["ChapterSummary | None"] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", uselist=False
    )


class ChapterContent(Base):
    __tablename__ = "chapter_content"

    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text, default="")

    chapter: Mapped["Chapter"] = relationship(back_populates="content")


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"

    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")

    chapter: Mapped["Chapter"] = relationship(back_populates="summary")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    md_content: Mapped[str] = mapped_column(Text, default="")  # 人物卡 markdown
    state_json: Mapped[str] = mapped_column(Text, default="{}")  # 当前状态(供 Agent)

    book: Mapped["Book"] = relationship(back_populates="characters")


class Worldbuilding(Base):
    __tablename__ = "worldbuilding"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64), default="general")
    title: Mapped[str] = mapped_column(String(255))
    md_content: Mapped[str] = mapped_column(Text, default="")


class PlotThread(Base):
    __tablename__ = "plot_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="open")  # open/active/closed
    description: Mapped[str] = mapped_column(Text, default="")


class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))  # book/chapter/outline...
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending/running/paused/done/error
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    error: Mapped[str] = mapped_column(Text, default="")
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmbeddingRef(Base):
    """SQLite 侧记录向量映射,实际向量存 Chroma。"""

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(32))  # chapter_summary/worldbuilding
    source_id: Mapped[int] = mapped_column(Integer)
    chroma_id: Mapped[str] = mapped_column(String(128))
