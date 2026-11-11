"""Temporary database, fake model and vector fixtures for memory tests."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app import database, models
from app.agents import pipeline, prompts
from app.api import books, chapters, characters, memory, volumes, worldbuilding
from app.db_base import Base
from app.memory import context, facts, sync, vector_store

SECRET = "Bearer private-provider-secret"


@dataclass
class FakeVectors:
    docs: dict[tuple[str, str], tuple[str, dict[str, object]]] = field(
        default_factory=dict
    )
    queries: list[dict[str, object] | None] = field(default_factory=list)
    fail_delete: bool = False

    def upsert(
        self,
        book_id: str,
        doc_id: str,
        _embedding: list[float],
        document: str,
        metadata: dict[str, object],
    ) -> None:
        self.docs[(book_id, doc_id)] = document, metadata

    def delete_source(self, book_id: str, source_type: str, source_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError(SECRET)
        for key, (_text, meta) in list(self.docs.items()):
            if (
                key[0] == book_id
                and meta.get("source_type") == source_type
                and meta.get("source_id") == source_id
            ):
                del self.docs[key]

    def query(
        self,
        book_id: str,
        _embedding: list[float],
        top_k: int = 5,
        where: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        self.queries.append(where)
        # Deliberately ignores the time predicate; SQL must reject older
        # metadata and future chapters even if the vector store returns them.
        filters = (where or {}).get("$and")
        source_type = where.get("source_type") if where else None
        if isinstance(filters, list) and filters:
            source_type = filters[0].get("source_type")
        return [
            {"document": text, "metadata": meta}
            for (owner, _doc_id), (text, meta) in self.docs.items()
            if owner == book_id and meta.get("source_type") == source_type
        ][:top_k]

    def existing_ids(self, book_id: str) -> set[str]:
        return {doc_id for owner, doc_id in self.docs if owner == book_id}

    def list_book_ids(self) -> list[str]:
        return list({book_id for book_id, _ in self.docs})

    def delete_book(self, book_id: str) -> None:
        for key in list(self.docs):
            if key[0] == book_id:
                del self.docs[key]


@dataclass
class FakeLLM:
    chats: list[str] = field(default_factory=list)
    chat_prompts: list[str] = field(default_factory=list)
    fact_replies: dict[str, str] = field(default_factory=dict)
    last_stream_prompt: str = ""
    embeddings: list[str] = field(default_factory=list)
    streams: int = 0
    body: str = "生成正文：桥边秘密"
    fail_chat: str | None = None
    fail_embed: str | None = None
    block_chat_for: str | None = None
    block_embed_for: str | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def chat(self, prompt: str, system: str = "") -> str:
        self.chats.append(system)
        self.chat_prompts.append(prompt)
        if system == self.fail_chat:
            raise RuntimeError(SECRET)
        if self.block_chat_for and self.block_chat_for in prompt:
            self.entered.set()
            await self.release.wait()
        if system == facts.FACTS_SYSTEM:
            return next(
                (
                    reply
                    for marker, reply in self.fact_replies.items()
                    if marker in prompt
                ),
                '{"characters":[],"threads":[]}',
            )
        if system == prompts.SUMMARY_SYSTEM:
            return "章摘要：" + prompt.split("\n\n", maxsplit=1)[-1]
        assert system == prompts.VOLUME_SUMMARY_SYSTEM
        return "卷摘要：" + prompt.split("\n\n", maxsplit=1)[-1]

    async def embed(self, text: str) -> list[float]:
        self.embeddings.append(text)
        if self.fail_embed and self.fail_embed in text:
            raise RuntimeError(SECRET)
        if self.block_embed_for and self.block_embed_for in text:
            self.entered.set()
            await self.release.wait()
        return [0.1, 0.2]

    async def chat_stream(self, _prompt: str, system: str = "") -> AsyncIterator[str]:
        assert system == prompts.CHAPTER_BODY_SYSTEM
        self.streams += 1
        self.last_stream_prompt = _prompt
        yield self.body


@pytest.fixture(name="storage")
def storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/memory.db")
    event.listen(engine, "connect", database._enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


@pytest.fixture(name="client")
async def client(storage: sessionmaker[Session]) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    for router in (
        books.router,
        chapters.router,
        characters.router,
        memory.router,
        volumes.router,
        worldbuilding.router,
    ):
        app.include_router(router)

    def session_override() -> Iterator[Session]:
        with storage() as db:
            yield db

    app.dependency_overrides[database.get_db] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture(name="fakes")
def fakes(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeLLM, FakeVectors]:
    llm, vectors = FakeLLM(), FakeVectors()
    monkeypatch.setattr(context, "get_llm", lambda: llm)
    monkeypatch.setattr(pipeline, "get_llm", lambda: llm)
    for name in (
        "upsert",
        "delete_source",
        "query",
        "existing_ids",
        "list_book_ids",
        "delete_book",
    ):
        monkeypatch.setattr(vector_store, name, getattr(vectors, name))
    return llm, vectors


def seed(db: Session) -> tuple[str, str, str, str]:
    book = models.Book(title="书")
    vol = models.Volume(book=book, title="卷", summary="计划含后续剧情")
    history = models.Chapter(book=book, volume=vol, title="过去章", order_index=1)
    target = models.Chapter(book=book, volume=vol, title="目标章", order_index=2)
    db.add(book)
    db.commit()
    return book.id, vol.id, history.id, target.id


async def drain(storage: sessionmaker[Session], book_id: str) -> None:
    """Process only this test's queued work in the same order as the worker."""
    with storage() as db:
        for _ in range(20):
            job = db.scalar(
                select(models.MemorySyncJob.id)
                .where(
                    models.MemorySyncJob.book_id == book_id,
                    models.MemorySyncJob.status == "pending",
                )
                .order_by(models.MemorySyncJob.source_type)
                .limit(1)
            )
            if job is None:
                return
            await sync.process_job(db, job)
    pytest.fail("memory queue did not drain")


async def chapter_context(storage: sessionmaker[Session], chapter_id: str) -> str:
    with storage() as db:
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None
        return await context.build_chapter_context(db, chapter)
