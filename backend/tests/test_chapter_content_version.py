"""Manual edits and generation cannot overwrite a newer chapter body."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import models, schemas
from app.agents import pipeline
from app.api import chapters, generate
from app.database import get_db
from app.db_base import Base
from app.tasks import book_generation


def memory_revisions(db: Session) -> set[tuple[str, str, str, str]]:
    return {
        (job.id, job.revision, job.source_hash, job.status)
        for job in db.scalars(select(models.MemorySyncJob))
    }


async def test_only_one_editor_can_save_against_the_same_content_version(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'versions.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(chapters.router)

    def session_for_request() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_for_request
    try:
        with sessions() as db:
            chapter = models.Chapter(
                book=models.Book(title="测试书"), title="第一章", outline="保留章纲"
            )
            db.add(chapter)
            db.commit()
            chapter_id = chapter.id
        path = f"/chapters/{chapter_id}/content"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            opened = (await client.get(path)).json()
            assert opened["body"] == ""
            original_version = opened["version"]
            assert original_version == (await client.get(path)).json()["version"]

            bodies = ["作者保存的正文\n", "作者保存的正文"]
            results = await asyncio.gather(
                *[
                    client.put(
                        path, json={"body": body, "expected_version": original_version}
                    )
                    for body in bodies
                ]
            )
            assert sorted(response.status_code for response in results) == [200, 409]
            saved = next(
                response.json() for response in results if response.status_code == 200
            )
            assert saved["version"] != original_version
            assert saved["status"] == "done" and saved["word_count"] == len(
                saved["body"]
            )

            with sessions() as db:
                current = db.get(models.Chapter, chapter_id)
                assert current is not None and current.outline == "保留章纲"
                revisions = memory_revisions(db)
                assert revisions
            stale = await client.put(
                path, json={"body": "", "expected_version": original_version}
            )
            assert stale.status_code == 409
            reread = (await client.get(path)).json()
            assert (reread["body"], reread["version"]) == (
                saved["body"],
                saved["version"],
            )
            with sessions() as db:
                assert memory_revisions(db) == revisions

            # A legacy client still saves without a version, and whitespace in
            # the saved text is reflected in the next version.
            other_body = next(body for body in bodies if body != saved["body"])
            legacy = await client.put(path, json={"body": other_body})
            assert legacy.status_code == 200 and legacy.json()["body"] == other_body
            assert legacy.json()["version"] != saved["version"]
    finally:
        engine.dispose()


async def test_manual_generation_rechecks_content_after_request_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'generation_versions.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(chapters.router)
    app.include_router(generate.router)

    def session_for_request() -> Iterator[Session]:
        with sessions() as db:
            yield db

    model_calls = 0

    async def fake_stream(_system: str, _user: str) -> AsyncIterator[pipeline.Event]:
        nonlocal model_calls
        model_calls += 1
        yield ("token", "合成正文")
        yield ("full", "合成正文")

    async def context(_db: Session, _chapter: models.Chapter) -> str:
        return "测试上下文"

    async def process_memory(_db: Session, _chapter: models.Chapter) -> bool:
        return True

    app.dependency_overrides[get_db] = session_for_request
    monkeypatch.setattr(pipeline, "_stream", fake_stream)
    monkeypatch.setattr(pipeline.memory, "build_chapter_context", context)
    monkeypatch.setattr(pipeline.memory_sync, "process_chapter_memory", process_memory)
    try:
        with sessions() as db:
            chapter = models.Chapter(book=models.Book(title="测试书"), title="第一章")
            db.add(chapter)
            db.commit()
            chapter_id = chapter.id
        path = f"/chapters/{chapter_id}/content"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            opened = (await client.get(path)).json()
            revisions: set[tuple[str, str, str, str]] = set()
            claim = book_generation.claim_manual_generation

            def save_during_admission(
                db: Session, book_id: str, operation: str
            ) -> book_generation.GenerationLease:
                # The request has already looked up its chapter. A writer may
                # still commit before the stream acquires the SQLite write lock.
                with sessions() as writer:
                    chapters.update_chapter_content(
                        chapter_id,
                        schemas.ChapterContentUpdate(
                            body="另一窗口保存的新稿",
                            expected_version=opened["version"],
                        ),
                        writer,
                    )
                    revisions.update(memory_revisions(writer))
                return claim(db, book_id, operation)

            with monkeypatch.context() as admission:
                admission.setattr(
                    book_generation, "claim_manual_generation", save_during_admission
                )
                stale = await client.post(
                    "/generate/chapter",
                    json={
                        "chapter_id": chapter_id,
                        "expected_version": opened["version"],
                    },
                )
            assert stale.status_code == 200
            assert (
                "event: content_conflict" in stale.text and "event: error" in stale.text
            )
            assert "event: token" not in stale.text and "event: done" not in stale.text
            assert model_calls == 0
            latest = (await client.get(path)).json()
            assert latest["body"] == "另一窗口保存的新稿"
            with sessions() as db:
                current = db.get(models.Chapter, chapter_id)
                assert current is not None and current.status == "done"
                assert memory_revisions(db) == revisions

            # A matching version and a legacy request may both generate.
            for version in ({"expected_version": latest["version"]}, {}):
                generated = await client.post(
                    "/generate/chapter", json={"chapter_id": chapter_id, **version}
                )
                assert generated.status_code == 200
                assert (
                    "event: done" in generated.text
                    and "event: error" not in generated.text
                )
                assert (await client.get(path)).json()["body"] == "合成正文"
            assert model_calls == 2
    finally:
        engine.dispose()
