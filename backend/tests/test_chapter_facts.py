"""Chronological character/thread state, evidence validation and fact repair."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.agents import pipeline, prompts, state
from app.memory import context, facts, sync
from tests.memory_support import FakeLLM, FakeVectors, chapter_context, drain, seed
from tests.memory_support import client as memory_client_fixture  # noqa: F401
from tests.memory_support import fakes as memory_fakes_fixture  # noqa: F401
from tests.memory_support import storage as memory_storage_fixture  # noqa: F401


def seed_cast(db: Session, book_id: str) -> tuple[str, str]:
    character = models.Character(
        book_id=book_id,
        name="阿青",
        md_content="从小在岸边长大。",
        state_json='{"location":"作者全局地点，不能泄漏到早章"}',
    )
    thread = models.PlotThread(
        book_id=book_id,
        name="岸边暗号",
        description="调查暗号的由来。",
        status="closed",
    )
    db.add_all([character, thread])
    db.commit()
    return character.id, thread.id


def reply(
    character_id: str,
    thread_id: str,
    *,
    evidence: str,
    location: str,
    status: facts.ThreadStatus,
    condition: str | None = None,
) -> str:
    patch = {"location": location}
    if condition:
        patch["condition"] = condition
    return json.dumps(
        {
            "characters": [
                {"character_id": character_id, "state": patch, "evidence": evidence}
            ],
            "threads": [
                {"thread_id": thread_id, "status": status, "evidence": evidence}
            ],
        },
        ensure_ascii=False,
    )


async def test_fact_replay_respects_live_order_and_keeps_author_fields(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, volume_id, history_id, target_id = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
        second = models.Volume(book_id=book_id, title="第二卷", order_index=1)
        later = models.Chapter(
            book_id=book_id, volume_id=volume_id, title="后章", order_index=3
        )
        db.add_all([second, later])
        db.commit()
        second_id, later_id = second.id, later.id

    past = "阿青身受轻伤，在前段港口发现了暗号。"
    future = "阿青在后续高塔查清了暗号。"
    llm.fact_replies[past] = reply(
        character_id,
        thread_id,
        evidence=past,
        location="前段港口",
        condition="轻伤",
        status="active",
    )
    llm.fact_replies[future] = reply(
        character_id, thread_id, evidence=future, location="后续高塔", status="closed"
    )
    for chapter_id, body in ((history_id, past), (later_id, future)):
        response = await client.put(
            f"/chapters/{chapter_id}/content", json={"body": body}
        )
        assert response.status_code == 200
    assert not llm.chats
    await drain(storage, book_id)

    retrieved = await chapter_context(storage, target_id)
    assert '"location": "前段港口"' in retrieved and '"condition": "轻伤"' in retrieved
    assert "(active) 岸边暗号" in retrieved
    assert "后续高塔" not in retrieved and "作者全局地点" not in retrieved
    characters_url = f"/books/{book_id}/characters"
    threads_url = f"/books/{book_id}/plot_threads"
    person = (await client.get(characters_url)).json()[0]
    assert person["derived_state"] == {"location": "后续高塔", "condition": "轻伤"}
    assert "作者全局地点" in person["state_json"]
    thread = (await client.get(threads_url)).json()[0]
    assert thread["status"] == "closed" and thread["derived_status"] == "closed"
    with storage() as db:
        assert not state.load_state(db, book_id).open_plot_threads

    edited = await client.patch(
        f"/characters/{character_id}", json={"state_json": '{"note":"作者修订"}'}
    )
    assert edited.json()["state_json"] == '{"note":"作者修订"}'
    assert edited.json()["derived_state"] == person["derived_state"]

    # Moving a chapter into another volume does not change when its facts apply.
    moved = await client.post(
        "/chapters/reorder",
        json={
            "items": [
                {"id": later_id, "order_index": 3, "volume_id": second_id},
            ]
        },
    )
    assert moved.status_code == 200
    await drain(storage, book_id)
    assert "后续高塔" not in await chapter_context(storage, target_id)
    # Moving the same chapter earlier makes its closure visible. A subsequent
    # advance event cannot reopen an already resolved thread.
    reordered = await client.post(
        "/chapters/reorder",
        json={
            "items": [
                {"id": later_id, "order_index": 0, "volume_id": second_id},
            ]
        },
    )
    assert reordered.status_code == 200
    await drain(storage, book_id)
    assert llm.chats.count(facts.FACTS_SYSTEM) == 2
    assert "(active) 岸边暗号" not in await chapter_context(storage, target_id)
    assert (await client.get(characters_url)).json()[0]["derived_state"][
        "location"
    ] == "前段港口"

    assert (await client.delete(f"/volumes/{second_id}")).status_code == 200
    await drain(storage, book_id)
    assert (await client.get(f"/chapters/{later_id}")).json()["volume_id"] is None
    assert (await client.get(threads_url)).json()[0]["derived_status"] == "closed"
    assert (await client.delete(f"/chapters/{later_id}")).status_code == 200
    await drain(storage, book_id)
    assert (await client.get(threads_url)).json()[0]["derived_status"] == "active"
    assert "(active) 岸边暗号" in await chapter_context(storage, target_id)
    with storage() as db:
        assert db.get(models.ChapterFacts, later_id) is None
        assert sync.find_job(db, book_id, sync.FACTS, later_id) is None
        assert state.load_state(db, book_id).open_plot_threads[0].status == "active"


@pytest.mark.parametrize("edit", ["rewrite", "delete", "reorder"])
async def test_context_reloads_story_facts_after_waiting_for_query_embedding(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    edit: str,
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, volume_id, chapter_id, target_id = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
    original_body = "阿青在旧山庄定位解开了暗号。"
    rewritten_body = "阿青在新渡口继续查暗号。"
    llm.fact_replies[original_body] = reply(
        character_id,
        thread_id,
        evidence=original_body,
        location="旧山庄定位",
        status="closed",
    )
    llm.fact_replies[rewritten_body] = reply(
        character_id,
        thread_id,
        evidence=rewritten_body,
        location="新渡口",
        status="active",
    )
    content_url = f"/chapters/{chapter_id}/content"
    assert (
        await client.put(content_url, json={"body": original_body})
    ).status_code == 200
    await drain(storage, book_id)
    assert '"location": "旧山庄定位"' in await chapter_context(storage, target_id)

    with storage() as db:
        target = db.get(models.Chapter, target_id)
        assert target is not None
        llm.block_embed_for = target.title
        pending = asyncio.create_task(context.build_chapter_context(db, target))
        try:
            await asyncio.wait_for(llm.entered.wait(), timeout=3)
            if edit == "rewrite":
                assert (
                    await client.put(content_url, json={"body": rewritten_body})
                ).status_code == 200
                await drain(storage, book_id)
            elif edit == "delete":
                assert (
                    await client.delete(f"/chapters/{chapter_id}")
                ).status_code == 200
            else:
                response = await client.post(
                    "/chapters/reorder",
                    json={
                        "items": [
                            {
                                "id": chapter_id,
                                "order_index": 3,
                                "volume_id": volume_id,
                            },
                        ]
                    },
                )
                assert response.status_code == 200
        finally:
            llm.release.set()
        recalled = await pending
    assert "旧山庄定位" not in recalled
    if edit == "rewrite":
        assert '"location": "新渡口"' in recalled and "(active) 岸边暗号" in recalled
    else:
        assert "前章确立的人物状态" not in recalled and "(open) 岸边暗号" in recalled


async def test_new_character_makes_completed_fact_job_repairable_without_a_body_edit(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, chapter_id, _ = seed(db)
    body = "阿青抵达北门。"
    assert (
        await client.put(f"/chapters/{chapter_id}/content", json={"body": body})
    ).status_code == 200
    await drain(storage, book_id)
    assert facts.FACTS_SYSTEM not in llm.chats
    with storage() as db:
        previous = db.get(models.ChapterFacts, chapter_id)
        assert previous is not None
        old_roster_hash = previous.roster_hash

    added = await client.post("/characters", json={"book_id": book_id, "name": "阿青"})
    assert added.status_code == 200
    character_id = added.json()["id"]
    llm.fact_replies[body] = json.dumps(
        {
            "characters": [
                {
                    "character_id": character_id,
                    "state": {"location": "北门"},
                    "evidence": body,
                }
            ],
            "threads": [],
        },
        ensure_ascii=False,
    )
    state_url = f"/books/{book_id}/memory/status"
    assert (await client.get(state_url)).json()["missing"] == 1
    repaired = await client.post(f"/books/{book_id}/memory/repair")
    assert (
        repaired.json()["pending"] == 1
        and repaired.json()["items"][0]["source_type"] == sync.FACTS
    )
    await drain(storage, book_id)
    assert llm.chats.count(facts.FACTS_SYSTEM) == 1
    assert (await client.get(state_url)).json()["missing"] == 0
    with storage() as db:
        assert facts.replay(db, book_id).characters == {
            character_id: {"location": "北门"}
        }
        saved = db.get(models.ChapterFacts, chapter_id)
        source = db.get(models.ChapterContent, chapter_id)
        assert saved is not None and source is not None
        assert (
            saved.source_hash == facts.fingerprint(body)
            and saved.roster_hash != old_roster_hash
        )
        assert source.body == body
        # The row remains tied to the actual body even if an import bypasses
        # the outbox and preserves the matching roster and job revision.
        source.body = "实际正文已被外部导入替换。"
        db.commit()
        assert not facts.replay(db, book_id).characters


@pytest.mark.parametrize("malformed_json", [False, True])
async def test_stored_fact_with_valid_hashes_and_bad_payload_is_missing_and_repairable(
    malformed_json: bool,
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, chapter_id, _ = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
    body = "阿青在北门拾到半截暗号。"
    llm.fact_replies[body] = reply(
        character_id, thread_id, evidence=body, location="北门", status="active"
    )
    content_url = f"/chapters/{chapter_id}/content"
    status_url = f"/books/{book_id}/memory/status"
    assert (await client.put(content_url, json={"body": body})).status_code == 200
    await drain(storage, book_id)
    assert (await client.get(status_url)).json()["missing"] == 0
    original_chats, original_embeddings = list(llm.chats), list(llm.embeddings)

    bad_evidence = "未出现在正文的私有片段"
    with storage() as db:
        saved = db.get(models.ChapterFacts, chapter_id)
        assert saved is not None
        original_hashes = (saved.source_hash, saved.roster_hash)
        saved.payload = (
            "{" + bad_evidence
            if malformed_json
            else reply(
                character_id,
                thread_id,
                evidence=bad_evidence,
                location="伪造地点",
                status="closed",
            )
        )
        db.commit()
        replayed = facts.replay(db, book_id)
        assert not replayed.characters and not replayed.threads
    assert bad_evidence not in caplog.text
    assert (await client.get(status_url)).json()["missing"] == 1
    assert llm.chats == original_chats

    submitted = await client.post(f"/books/{book_id}/memory/repair")
    assert submitted.json()["pending"] == 1
    assert submitted.json()["items"][0]["source_type"] == sync.FACTS
    await drain(storage, book_id)
    assert (await client.get(status_url)).json()["missing"] == 0
    assert (await client.get(content_url)).json()["body"] == body
    assert llm.chats == [*original_chats, facts.FACTS_SYSTEM]
    assert llm.embeddings == original_embeddings
    with storage() as db:
        saved = db.get(models.ChapterFacts, chapter_id)
        assert (
            saved is not None
            and (saved.source_hash, saved.roster_hash) == original_hashes
        )
        replayed = facts.replay(db, book_id)
        assert replayed.characters == {character_id: {"location": "北门"}}
        assert replayed.threads == {thread_id: "active"}


async def test_roster_change_during_extraction_requeues_and_discards_old_reply(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, chapter_id, _ = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
    body = "阿青和阿宁抵达北门调查暗号。"
    llm.fact_replies[body] = reply(
        character_id, thread_id, evidence=body, location="北门", status="active"
    )
    assert (
        await client.put(f"/chapters/{chapter_id}/content", json={"body": body})
    ).status_code == 200
    llm.block_chat_for = body
    with storage() as db:
        job = sync.find_job(db, book_id, sync.FACTS, chapter_id)
        assert job is not None
        old_revision = job.revision
        pending = asyncio.create_task(sync.process_job(db, job.id))
        try:
            await asyncio.wait_for(llm.entered.wait(), timeout=3)
            added = await client.post(
                "/characters", json={"book_id": book_id, "name": "阿宁"}
            )
            assert added.status_code == 200
            added_id = added.json()["id"]
        finally:
            llm.release.set()
        assert not await pending
        refreshed = sync.find_job(db, book_id, sync.FACTS, chapter_id)
        assert (
            refreshed is not None
            and refreshed.revision != old_revision
            and refreshed.status == "pending"
        )
        assert db.get(models.ChapterFacts, chapter_id) is None
    batch = json.loads(llm.fact_replies[body])
    batch["characters"].append(
        {"character_id": added_id, "state": {"location": "北门"}, "evidence": body}
    )
    llm.fact_replies[body] = json.dumps(batch, ensure_ascii=False)
    await drain(storage, book_id)
    with storage() as db:
        assert facts.replay(db, book_id).characters == {
            character_id: {"location": "北门"},
            added_id: {"location": "北门"},
        }


async def test_invalid_facts_are_retryable_and_revisions_rebuild_without_rewriting_prose(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, chapter_id, target_id = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
        llm.body = "阿青在城门找到暗号。"
        llm.fact_replies[llm.body] = reply(
            character_id,
            thread_id,
            evidence="模型编造的证据",
            location="城门",
            status="active",
        )
        events = [event async for event in pipeline.gen_chapter_body(db, chapter_id)]
        assert events[-1] == ("done", "")
        assert any(
            event == "log" and "正文已保存" in message for event, message in events
        )
        assert db.get(models.ChapterFacts, chapter_id) is None
    status_url = f"/books/{book_id}/memory/status"
    repair_url = f"/books/{book_id}/memory/repair"
    content_url = f"/chapters/{chapter_id}/content"
    state_json = (await client.get(status_url)).json()
    failed = state_json["items"][0]
    assert state_json["failed"] == 1 and failed["source_type"] == sync.FACTS
    assert "证据不符合本章" in failed["error"] and "模型编造的证据" not in str(
        state_json
    )
    assert (await client.get(content_url)).json()["body"] == llm.body

    llm.fact_replies[llm.body] = reply(
        character_id, thread_id, evidence=llm.body, location="城门", status="active"
    )
    assert (await client.post(repair_url)).json()["pending"] == 1
    await drain(storage, book_id)
    assert '"location": "城门"' in await chapter_context(storage, target_id)
    assert llm.streams == 1 and llm.chats.count(prompts.SUMMARY_SYSTEM) == 1
    assert (await client.get(content_url)).json()["body"] == llm.body

    # A retry can restore the job state from a still-valid stored fact batch.
    with storage() as db:
        job = sync.enqueue(db, book_id, sync.FACTS, chapter_id, force=True)
        db.commit()
        before = llm.chats.count(facts.FACTS_SYSTEM)
        assert await sync.process_job(db, job.id)
        assert llm.chats.count(facts.FACTS_SYSTEM) == before

    rewritten = "阿青在渡口找到暗号。"
    llm.fact_replies[rewritten] = reply(
        character_id, thread_id, evidence=rewritten, location="渡口", status="active"
    )
    assert (await client.put(content_url, json={"body": rewritten})).status_code == 200
    assert '"location": "城门"' not in await chapter_context(storage, target_id)
    await drain(storage, book_id)
    assert '"location": "渡口"' in await chapter_context(storage, target_id)
    assert (await client.put(content_url, json={"body": " \u3000"})).status_code == 200
    assert '"location": "渡口"' not in await chapter_context(storage, target_id)
    with storage() as db:
        assert db.get(models.ChapterFacts, chapter_id) is None


@pytest.mark.parametrize("delete", [False, True])
async def test_late_fact_reply_cannot_republish_rewritten_or_deleted_content(
    storage: sessionmaker[Session],
    client: httpx.AsyncClient,
    fakes: tuple[FakeLLM, FakeVectors],
    delete: bool,
) -> None:
    llm, _ = fakes
    with storage() as db:
        book_id, _, chapter_id, target_id = seed(db)
        character_id, thread_id = seed_cast(db, book_id)
    old_body = "阿青在待撤回的山庄确认暗号。"
    new_body = "阿青在更新的山脚确认暗号。"
    llm.fact_replies[old_body] = reply(
        character_id,
        thread_id,
        evidence=old_body,
        location="待撤回的山庄",
        status="closed",
    )
    llm.fact_replies[new_body] = reply(
        character_id,
        thread_id,
        evidence=new_body,
        location="更新的山脚",
        status="active",
    )
    content_url = f"/chapters/{chapter_id}/content"
    assert (await client.put(content_url, json={"body": old_body})).status_code == 200
    llm.block_chat_for = old_body
    with storage() as db:
        job = sync.find_job(db, book_id, sync.FACTS, chapter_id)
        assert job is not None
        pending = asyncio.create_task(sync.process_job(db, job.id))
        await asyncio.wait_for(llm.entered.wait(), timeout=3)
        if delete:
            assert (await client.delete(f"/chapters/{chapter_id}")).status_code == 200
        else:
            assert (
                await client.put(content_url, json={"body": new_body})
            ).status_code == 200
        llm.release.set()
        assert not await pending
    await drain(storage, book_id)
    retrieved = await chapter_context(storage, target_id)
    assert "待撤回的山庄" not in retrieved
    assert ('"location": "更新的山脚"' in retrieved) is (not delete)


@pytest.mark.parametrize(
    "invalid",
    [
        "extra-field",
        "unknown-role",
        "unknown-thread",
        "duplicate",
        "bad-evidence",
        "status",
    ],
)
def test_fact_contract_rejects_untrusted_or_ungrounded_updates(invalid: str) -> None:
    character: dict[str, object] = {
        "character_id": "hero",
        "state": {"location": "港口"},
        "evidence": "她到了港口",
    }
    thread: dict[str, object] = {
        "thread_id": "clue",
        "status": "active",
        "evidence": "看见暗号",
    }
    characters = [character]
    if invalid == "extra-field":
        character["state"] = {"secret": "未授权字段"}
    elif invalid == "unknown-role":
        character["character_id"] = "foreign-character"
    elif invalid == "unknown-thread":
        thread["thread_id"] = "foreign-thread"
    elif invalid == "duplicate":
        characters.append(character.copy())
    elif invalid == "bad-evidence":
        character["evidence"] = "后章才出现的地点"
    else:
        thread["status"] = "open"
    with pytest.raises((facts.InvalidChapterFacts, ValidationError)):
        batch = facts.FactBatch.model_validate(
            {"characters": characters, "threads": [thread]}
        )
        facts.validate(
            batch,
            "她到了港口，看见暗号。",
            facts.Roster({"hero": "阿青"}, {"clue": ("暗号", "")}),
        )
