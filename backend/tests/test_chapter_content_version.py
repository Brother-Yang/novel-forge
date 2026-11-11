"""Manual saves from separate requests cannot overwrite a newer chapter body."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.api import chapters
from app.database import get_db
from app.db_base import Base


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
