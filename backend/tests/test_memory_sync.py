"""Memory repair boundaries with temporary SQLite, fake LLM and fake vectors."""

import asyncio
import logging

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.agents import pipeline, prompts
from app.config import settings
from app.memory import context, sync, vector_store
from tests.memory_support import (
    SECRET,
    FakeLLM,
    FakeVectors,
    chapter_context,
    drain,
    seed,
)
from tests.memory_support import client as memory_client_fixture  # noqa: F401
from tests.memory_support import fakes as memory_fakes_fixture  # noqa: F401
from tests.memory_support import storage as memory_storage_fixture  # noqa: F401


async def test_progress_reads_only_queue_and_keeps_full_audit_separate(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _llm, _vectors = fakes
    with storage() as db:
        book_id, _, chapter_id, _ = seed(db)
        other = models.Book(title="他书")
        db.add(other)
        db.flush()
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None
        chapter.content = models.ChapterContent(body="历史旧正文，需要全量审计才能发现")
        chapter.status = "done"
        db.add_all(
            [
                models.MemorySyncJob(
                    book_id=book_id,
                    source_type=sync.WORLD,
                    source_id=models.new_id(),
                    source_hash="queue-only",
                    status=status,
                )
                for status in ("pending", "running", "error", "done")
            ]
        )
        db.add(
            models.MemorySyncJob(
                book_id=other.id, source_type=sync.WORLD, source_id=models.new_id()
            )
        )
        db.commit()

    audited = (await client.get(f"/books/{book_id}/memory/status")).json()
    assert audited["missing"] > 0

    def forbid_vectors(_book_id: str) -> set[str]:
        pytest.fail("progress must not read Chroma")

    def inspect_sql(
        _conn: object, _cursor: object, statement: str, *_args: object
    ) -> None:
        assert not any(
            name in statement.lower()
            for name in (
                "chapter_content",
                "chapter_summaries",
                "chapter_facts",
                "worldbuilding",
                "embeddings",
                "volumes",
            )
        ), statement

    monkeypatch.setattr(vector_store, "existing_ids", forbid_vectors)
    engine = storage.kw["bind"]
    assert isinstance(engine, Engine)
    event.listen(engine, "before_cursor_execute", inspect_sql)
    try:
        response = await client.get(f"/books/{book_id}/memory/progress")
    finally:
        event.remove(engine, "before_cursor_execute", inspect_sql)
    assert response.status_code == 200
    progress = response.json()
    assert "missing" not in progress
    assert {
        key: progress[key] for key in ("pending", "running", "failed", "ready", "total")
    } == {
        "pending": 1,
        "running": 1,
        "failed": 1,
        "ready": 1,
        "total": 4,
    }
    assert {item["status"] for item in progress["items"]} == {
        "pending",
        "running",
        "error",
    }
    assert (await client.get("/books/missing/memory/progress")).status_code == 404


async def test_world_crud_hides_stale_vectors_even_when_storage_delete_fails(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, _, _, target_id = seed(db)
    status_url = f"/books/{book_id}/memory/status"
    repair_url = f"/books/{book_id}/memory/repair"
    response = await client.post(
        "/worldbuilding",
        json={"book_id": book_id, "title": "城堡", "md_content": "旧设定"},
    )
    assert response.status_code == 200
    item_url = f"/worldbuilding/{response.json()['id']}"
    assert not llm.embeddings
    assert (await client.get(status_url)).json()["pending"] == 1
    await drain(storage, book_id)
    assert "旧设定" in await chapter_context(storage, target_id)

    assert (
        await client.patch(item_url, json={"md_content": "新设定"})
    ).status_code == 200
    assert "旧设定" not in await chapter_context(storage, target_id)
    await drain(storage, book_id)
    assert "新设定" in await chapter_context(storage, target_id)
    vectors.fail_delete = True
    assert (await client.delete(item_url)).status_code == 200
    await drain(storage, book_id)
    assert vectors.existing_ids(book_id)  # Chroma still contains the deleted data.
    assert "新设定" not in await chapter_context(storage, target_id)
    state = (await client.get(status_url)).json()
    assert state["failed"] == 1
    assert state["items"][0]["source_type"] == sync.WORLD
    assert SECRET not in str(state) and SECRET not in caplog.text
    assert book_id in caplog.text and state["items"][0]["id"] in caplog.text
    vectors.fail_delete = False
    assert (await client.post(repair_url)).json()["pending"] == 1
    await drain(storage, book_id)
    assert not vectors.existing_ids(book_id)
    assert (await client.get(status_url)).json()["total"] == 0


async def test_manual_rewrite_failed_summary_preserves_body_then_repairs(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, volume_id, history_id, target_id = seed(db)
    history_url = f"/chapters/{history_id}/content"
    status_url = f"/books/{book_id}/memory/status"
    assert (
        await client.put(history_url, json={"body": "旧事实：有城堡"})
    ).status_code == 200
    assert not llm.chats
    await drain(storage, book_id)
    assert "旧事实：有城堡" in await chapter_context(storage, target_id)

    llm.fail_chat = prompts.SUMMARY_SYSTEM
    assert (
        await client.put(history_url, json={"body": "新事实：有港口"})
    ).status_code == 200
    assert "旧事实" not in await chapter_context(storage, target_id)
    await drain(storage, book_id)
    state = (await client.get(status_url)).json()
    assert state["failed"] == 2  # Chapter summary and its dependent volume summary.
    assert (await client.get(history_url)).json()["body"] == "新事实：有港口"
    llm.fail_chat = None
    assert (await client.post(f"/books/{book_id}/memory/repair")).json()["pending"] == 2
    await drain(storage, book_id)
    assert "新事实：有港口" in await chapter_context(storage, target_id)
    assert "旧事实" not in await chapter_context(storage, target_id)
    with storage() as db:
        volume = db.get(models.Volume, volume_id)
        assert volume is not None
        assert volume.summary == "计划含后续剧情"
        assert "新事实：有港口" in volume.memory_summary

    # Blank body and deleting the chapter both invalidate its derived memory.
    assert (await client.put(history_url, json={"body": " \u3000"})).status_code == 200
    assert "新事实" not in await chapter_context(storage, target_id)
    await drain(storage, book_id)
    assert (
        await client.put(history_url, json={"body": "将删除的正文"})
    ).status_code == 200
    await drain(storage, book_id)
    assert "将删除的正文" in await chapter_context(storage, target_id)
    assert (await client.delete(f"/chapters/{history_id}")).status_code == 200
    assert "将删除的正文" not in await chapter_context(storage, target_id)
    await drain(storage, book_id)
    with storage() as db:
        assert db.get(models.ChapterSummary, history_id) is None
        assert (
            db.scalar(
                select(models.EmbeddingRef).where(
                    models.EmbeddingRef.source_id == history_id
                )
            )
            is None
        )


@pytest.mark.parametrize("failure", ["embedding", "volume"])
async def test_generated_body_stays_done_while_memory_repair_retries_only_failed_work(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    failure: str,
) -> None:
    llm, _ = fakes
    if failure == "embedding":
        llm.fail_embed = "章摘要"
    else:
        llm.fail_chat = prompts.VOLUME_SUMMARY_SYSTEM
    with storage() as db:
        book_id, _, history_id, _ = seed(db)
        events = [event async for event in pipeline.gen_chapter_body(db, history_id)]
        chapter = db.get(models.Chapter, history_id)
        assert (
            chapter is not None
            and chapter.status == "done"
            and chapter.content is not None
        )
        assert chapter.content.body == llm.body and sync.valid_summary(chapter)
    assert events[-1] == ("done", "")
    assert any(event == "log" and "正文已保存" in message for event, message in events)
    state = (await client.get(f"/books/{book_id}/memory/status")).json()
    assert state["failed"] == 1 and state["ready"] == 2
    assert SECRET not in str(state)
    llm.fail_embed = llm.fail_chat = None
    await client.post(f"/books/{book_id}/memory/repair")
    await drain(storage, book_id)
    assert (await client.get(f"/books/{book_id}/memory/status")).json()["ready"] == 3
    assert llm.streams == 1 and llm.chats.count(prompts.SUMMARY_SYSTEM) == 1
    assert (await client.get(f"/chapters/{history_id}/content")).json()[
        "body"
    ] == llm.body


async def test_late_summary_cannot_replace_newer_sql_or_vector_revision(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, _, history_id, target_id = seed(db)
    content_url = f"/chapters/{history_id}/content"
    await client.put(content_url, json={"body": "第一版旧正文"})
    llm.block_chat_for = "第一版旧正文"
    with storage() as old_db:
        job = sync.find_job(old_db, book_id, sync.CHAPTER, history_id)
        assert job is not None
        old_result = asyncio.create_task(sync.process_job(old_db, job.id))
        await asyncio.wait_for(llm.entered.wait(), timeout=3)
        await client.put(content_url, json={"body": "第二版新正文"})
        await drain(storage, book_id)
        llm.release.set()
        assert not await old_result
    retrieved = await chapter_context(storage, target_id)
    assert "第二版新正文" in retrieved and "第一版旧正文" not in retrieved
    assert len(vectors.existing_ids(book_id)) == 1


async def test_generation_waits_for_the_same_revision_already_claimed_by_worker(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, history_id, target_id = seed(db)
    await client.put(f"/chapters/{history_id}/content", json={"body": "等候前情写入"})
    llm.block_chat_for = "等候前情写入"
    with storage() as worker_db, storage() as pipeline_db:
        job = sync.find_job(worker_db, book_id, sync.CHAPTER, history_id)
        chapter = pipeline_db.get(models.Chapter, history_id)
        assert job is not None and chapter is not None
        worker = asyncio.create_task(sync.process_job(worker_db, job.id))
        await asyncio.wait_for(llm.entered.wait(), timeout=3)
        generation = asyncio.create_task(
            sync.process_chapter_memory(pipeline_db, chapter)
        )
        await asyncio.sleep(0)  # The caller enters the already-running model task.
        assert not generation.done()
        assert not worker_db.in_transaction() and not pipeline_db.in_transaction()
        llm.release.set()
        assert await worker
        assert await generation
    assert llm.chats.count(prompts.SUMMARY_SYSTEM) == 1
    assert "等候前情写入" in await chapter_context(storage, target_id)


async def test_rewriting_early_chapter_excludes_same_and_later_facts_and_volume_fallback(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, volume_id, history_id, target_id = seed(db)
        for chapter_id, body in ((history_id, "先前事实"), (target_id, "本章旧事实")):
            chapter = db.get(models.Chapter, chapter_id)
            assert chapter is not None
            chapter.content = models.ChapterContent(body=body)
            chapter.status = "done"
            sync.enqueue_chapter(db, chapter)
        later = models.Chapter(
            book_id=book_id,
            volume_id=volume_id,
            title="后章",
            order_index=3,
            content=models.ChapterContent(body="后续真相"),
            status="done",
        )
        db.add(later)
        db.flush()
        sync.enqueue_chapter(db, later)
        db.commit()
    await drain(storage, book_id)
    assert "先前事实" in await chapter_context(storage, target_id)
    assert {
        "$and": [{"source_type": sync.CHAPTER}, {"source_id": {"$in": [history_id]}}]
    } in vectors.queries
    monkeypatch.setattr(settings, "context_token_budget", 72)
    retrieved = await chapter_context(storage, target_id)
    assert "本章旧事实" not in retrieved and "后续真相" not in retrieved
    assert "卷级摘要" not in retrieved and "计划含后续剧情" not in retrieved
    before = len(llm.embeddings)
    response = await client.post(
        "/chapters/reorder",
        json={
            "items": [
                {"id": target_id, "order_index": 0, "volume_id": volume_id},
            ]
        },
    )
    assert response.status_code == 200
    await drain(storage, book_id)
    assert len(llm.embeddings) == before
    assert "先前事实" not in await chapter_context(storage, target_id)


async def test_move_rename_and_delete_volume_refresh_only_affected_derived_memory(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, original_id, history_id, target_id = seed(db)
        second = models.Volume(book_id=book_id, title="归入卷")
        db.add(second)
        db.commit()
        second_id = second.id
    content_url = f"/chapters/{history_id}/content"
    assert (
        await client.put(content_url, json={"body": "不会重写的正文"})
    ).status_code == 200
    await drain(storage, book_id)
    before_vectors = vectors.existing_ids(book_id)
    before_embeddings = len(llm.embeddings)
    moved = await client.post(
        "/chapters/reorder",
        json={
            "items": [
                {"id": history_id, "order_index": 1, "volume_id": second_id},
                {"id": target_id, "order_index": 2, "volume_id": original_id},
            ]
        },
    )
    assert moved.status_code == 200
    await drain(storage, book_id)
    assert vectors.existing_ids(book_id) == before_vectors
    assert len(llm.embeddings) == before_embeddings
    with storage() as db:
        original = db.get(models.Volume, original_id)
        destination = db.get(models.Volume, second_id)
        assert original is not None and original.memory_summary == ""
        assert (
            destination is not None and "不会重写的正文" in destination.memory_summary
        )

    renamed = await client.patch(f"/chapters/{history_id}", json={"title": "新章名"})
    assert renamed.status_code == 200
    await drain(storage, book_id)
    assert llm.chats.count(prompts.SUMMARY_SYSTEM) == 1
    assert len(llm.embeddings) == before_embeddings + 1
    assert "新章名" in llm.embeddings[-1]

    volume_url = f"/volumes/{second_id}"
    assert (await client.patch(volume_url, json={"title": "新卷名"})).status_code == 200
    await drain(storage, book_id)
    assert "新卷名" in llm.chat_prompts[-1]
    assert (await client.delete(volume_url)).status_code == 200
    await drain(storage, book_id)
    assert len(llm.embeddings) == before_embeddings + 1
    chapter = (await client.get(f"/chapters/{history_id}")).json()
    assert chapter["volume_id"] is None
    assert (await client.get(content_url)).json()["body"] == "不会重写的正文"
    assert "不会重写的正文" in await chapter_context(storage, target_id)


async def test_repair_finds_legacy_rows_and_missing_chroma_documents(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, _, history_id, target_id = seed(db)
        chapter = db.get(models.Chapter, history_id)
        assert chapter is not None
        chapter.content = models.ChapterContent(body="实际旧书正文")
        chapter.status = "done"
        chapter.summary = models.ChapterSummary(summary="无法验证的旧摘要")
        item = models.Worldbuilding(
            book_id=book_id, title="旧书设定", md_content="仍然有效"
        )
        db.add(item)
        db.commit()
        vectors.upsert(
            book_id,
            "legacy-doc",
            [0.1],
            "不可信旧向量",
            {"source_type": sync.WORLD, "source_id": item.id},
        )
    status_url = f"/books/{book_id}/memory/status"
    repair_url = f"/books/{book_id}/memory/repair"
    assert (await client.get(status_url)).json()["missing"] == 4
    retrieved = await chapter_context(storage, target_id)
    assert "无法验证" not in retrieved and "不可信" not in retrieved
    assert (await client.post(repair_url)).json()["pending"] == 4 and not llm.chats
    await drain(storage, book_id)
    retrieved = await chapter_context(storage, target_id)
    assert "实际旧书正文" in retrieved and "仍然有效" in retrieved
    assert (await client.get(status_url)).json()["ready"] == 4

    vectors.docs.clear()
    assert (await client.get(status_url)).json()["missing"] == 2
    assert (await client.post(repair_url)).json()["pending"] == 2
    await drain(storage, book_id)
    assert (await client.get(status_url)).json()["ready"] == 4
    assert llm.chats.count(prompts.SUMMARY_SYSTEM) == 1 and llm.streams == 0


async def test_repair_removes_legacy_ref_to_another_book_without_indexing_it(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, _, _, _ = seed(db)
        foreign = models.Book(title="他书")
        db.add(foreign)
        db.flush()
        item = models.Worldbuilding(
            book_id=foreign.id, title="他书设定", md_content="私有内容"
        )
        db.add(item)
        db.flush()
        foreign_id, item_id = foreign.id, item.id
        db.add(
            models.EmbeddingRef(
                book_id=book_id,
                source_type=sync.WORLD,
                source_id=item_id,
                chroma_id="misfiled",
            )
        )
        db.commit()
    metadata: dict[str, object] = {"source_type": sync.WORLD, "source_id": item_id}
    vectors.upsert(book_id, "misfiled", [0.1], "私有内容", metadata)
    vectors.upsert(foreign_id, "valid-owner", [0.1], "私有内容", metadata)
    assert (await client.get(f"/books/{book_id}/memory/status")).json()["missing"] == 1
    await client.post(f"/books/{book_id}/memory/repair")
    await drain(storage, book_id)
    assert not llm.embeddings
    assert not vectors.existing_ids(book_id)
    assert vectors.existing_ids(foreign_id) == {"valid-owner"}


async def test_worker_recovers_claim_and_stops_memory_before_book_delete(
    storage: sessionmaker[Session],
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, vectors = fakes
    with storage() as db:
        book_id, _, _, _ = seed(db)
        item = models.Worldbuilding(book_id=book_id, title="停在嵌入")
        db.add(item)
        db.flush()
        job = sync.enqueue(db, book_id, sync.WORLD, item.id)
        job.status = "running"  # Previous process died after claim.
        db.commit()
        job_id = job.id
    vectors.upsert("orphan-book", "legacy", [0.1], "旧孤儿", {})
    llm.block_embed_for = "停在嵌入"
    await sync.start_memory_worker()
    try:
        await asyncio.wait_for(llm.entered.wait(), timeout=3)
        assert not vectors.existing_ids("orphan-book")
        await sync.suspend_book_memory(book_id)
        with storage() as db:
            saved_job = db.get(models.MemorySyncJob, job_id)
            assert saved_job is not None and saved_job.status == "pending"
            book = db.get(models.Book, book_id)
            assert book is not None
            db.delete(book)
            db.commit()
            assert db.get(models.MemorySyncJob, job_id) is None
        sync.resume_book_memory(book_id)
        llm.release.set()
        assert not vectors.existing_ids(book_id)
    finally:
        await sync.stop_memory_worker()


async def test_rag_degradation_reports_context_without_leaking_provider_details(
    storage: sessionmaker[Session],
    fakes: tuple[FakeLLM, FakeVectors],
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, _, target_id = seed(db)
    llm.fail_embed = "目标章"
    with caplog.at_level(logging.WARNING):
        retrieved = await chapter_context(storage, target_id)
    assert context.MEMORY_DEGRADED_NOTE in retrieved
    assert (
        book_id in caplog.text
        and target_id in caplog.text
        and "RuntimeError" in caplog.text
    )
    assert SECRET not in caplog.text and SECRET not in retrieved
