import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db_base import Base

BOOK_ID_LEN = 36


def new_book_id() -> str:
    return str(uuid.uuid4())


class Book(Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), primary_key=True, default=new_book_id
    )
    title: Mapped[str] = mapped_column(String(255))
    intro: Mapped[str] = mapped_column(Text, default="")
    brief: Mapped[str] = mapped_column(Text, default="")  # 一句话出书的原始输入
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
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
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
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    volume_id: Mapped[int | None] = mapped_column(
        ForeignKey("volumes.id", ondelete="SET NULL"), nullable=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(255))
    outline: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="empty")  # empty/outline/drafting/done
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    # 智能体安全护栏用:章节已 done 后默认不允许覆盖重写(高风险操作,会丢弃
    # 已有正文);仅当 review_content 对该章给出 needs_revision=true 时置为
    # True,write_chapter 消费一次后立即复位,避免无限期开着口子。
    revision_approved: Mapped[bool] = mapped_column(Boolean, default=False)

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
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(128))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    md_content: Mapped[str] = mapped_column(Text, default="")  # 人物卡 markdown
    state_json: Mapped[str] = mapped_column(Text, default="{}")  # 当前状态(供 Agent)

    book: Mapped["Book"] = relationship(back_populates="characters")


class Worldbuilding(Base):
    __tablename__ = "worldbuilding"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    category: Mapped[str] = mapped_column(String(64), default="general")
    title: Mapped[str] = mapped_column(String(255))
    md_content: Mapped[str] = mapped_column(Text, default="")


class PlotThread(Base):
    __tablename__ = "plot_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="open")  # open/active/closed
    description: Mapped[str] = mapped_column(Text, default="")


class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(64))  # book/chapter/outline...
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending/running/paused/done/error
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    error: Mapped[str] = mapped_column(Text, default="")
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentTraceEntry(Base):
    """智能体决策循环的完整执行轨迹(架构文档里的 Observability 模块)。

    GenerationTask.checkpoint_json 里的 decisions 字段只保留最近 30 条给 UI
    快速预览;这里额外落一份不截断的完整记录,供事后排查"Agent 到底做了
    什么、为什么做出某个决定"。
    """

    __tablename__ = "agent_trace_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="CASCADE")
    )
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(32))  # thought/decision/observation/log/error
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmbeddingRef(Base):
    """SQLite 侧记录向量映射,实际向量存 Chroma。"""

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(BOOK_ID_LEN), ForeignKey("books.id", ondelete="CASCADE")
    )
    source_type: Mapped[str] = mapped_column(String(32))  # chapter_summary/worldbuilding
    source_id: Mapped[int] = mapped_column(Integer)
    chroma_id: Mapped[str] = mapped_column(String(128))
