"""Exercise the local Chroma adapter with numeric vectors and no model access."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from chromadb.api.types import DefaultEmbeddingFunction
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.llm import get_embedding_profile, get_llm
from app.llm.openai_client import OpenAIClient
from app.memory import sync, vector_store
from tests.memory_support import FakeLLM, drain, seed
from tests.memory_support import client as memory_client_fixture  # noqa: F401
from tests.memory_support import storage as memory_storage_fixture  # noqa: F401


@pytest.fixture
def chroma_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "vectors"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vector_store.settings, "chroma_dir", str(path))

    def forbid_default(self: DefaultEmbeddingFunction, input: object) -> None:
        pytest.fail("the adapter must never invoke Chroma's default model")

    monkeypatch.setattr(DefaultEmbeddingFunction, "__call__", forbid_default)
    vector_store._client.cache_clear()
    try:
        yield path
    finally:
        vector_store._client.cache_clear()


def test_profile_tracks_effective_embedding_provider_model_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = vector_store.settings
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "https://example.com/v1/")
    monkeypatch.setattr(settings, "embedding_model", "fixture-embedding-a")

    def current_profile() -> str:
        get_llm.cache_clear()
        return get_embedding_profile()

    try:
        original = current_profile()
        assert len(original) == 64 and all(
            char in "0123456789abcdef" for char in original
        )
        monkeypatch.setattr(
            settings, "openai_base_url", "https://fixture:secret@EXAMPLE.COM:443/v1"
        )
        monkeypatch.setattr(settings, "openai_api_key", "synthetic-rotated-api-key")
        monkeypatch.setattr(settings, "generation_model", "fixture-generation-model")
        assert current_profile() == original

        with monkeypatch.context() as route:
            route.setattr(
                OpenAIClient,
                "embedding_endpoint",
                "https://example.com/v1/alternative-embeddings",
            )
            assert current_profile() != original

        monkeypatch.setattr(settings, "openai_base_url", "https://other.example.com/v1")
        assert current_profile() != original
        monkeypatch.setattr(settings, "openai_base_url", "https://example.com/v1")
        monkeypatch.setattr(settings, "embedding_model", "fixture-embedding-b")
        assert current_profile() != original

        monkeypatch.setattr(settings, "dashscope_base_url", "https://example.com/v1")
        monkeypatch.setattr(settings, "bailian_embedding_model", "fixture-embedding-a")
        monkeypatch.setattr(settings, "llm_provider", "bailian")
        bailian = current_profile()
        assert bailian != original
        monkeypatch.setattr(settings, "llm_provider", "dashscope")
        assert current_profile() == bailian
    finally:
        get_llm.cache_clear()


def test_numeric_collection_uses_local_backend_even_with_http_in_environment(
    chroma_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHROMA_API_IMPL", "chromadb.api.fastapi.FastAPI")
    monkeypatch.delenv("CHROMA_SERVER_HOST", raising=False)
    monkeypatch.delenv("CHROMA_SERVER_HTTP_PORT", raising=False)
    book_id = "new-book"

    vector_store.upsert(book_id, "doc", [0.1, 0.2], "正文", {"source_type": "chapter"})
    assert (chroma_path / "chroma.sqlite3").is_file()
    assert vector_store.query(book_id, [0.1, 0.2]) == [
        {
            "document": "正文",
            "metadata": {
                "source_type": "chapter",
                "embedding_profile": vector_store.get_embedding_profile(),
            },
        },
    ]
    collection = vector_store._collection(book_id)
    assert collection.configuration_json["embedding_function"] is None


def test_switching_profiles_preserves_legacy_and_accepts_a_different_dimension(
    chroma_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = "legacy_book"
    old_profile, current_profile = "a" * 64, "b" * 64
    metadata = {"source_type": "chapter_summary", "source_id": "source"}
    # Earlier Novel Forge versions created collections with Chroma's default.
    client = vector_store._client()
    legacy = client.create_collection(f"book_{book_id}")
    legacy.upsert(
        ids=["existing"],
        embeddings=[[0.1, 0.2]],
        documents=["旧正文"],
        metadatas=[metadata],
    )
    assert legacy.configuration_json["embedding_function"]["name"] == "default"

    monkeypatch.setattr(vector_store, "get_embedding_profile", lambda: old_profile)
    assert not vector_store.existing_ids(book_id)
    assert not vector_store.query(book_id, [0.1, 0.2])
    vector_store.upsert(book_id, "old", [0.3, 0.4], "先前版本", metadata)
    assert vector_store.existing_ids(book_id) == {"old"}

    monkeypatch.setattr(vector_store, "get_embedding_profile", lambda: current_profile)
    assert not vector_store.existing_ids(book_id)
    assert not vector_store.query(book_id, [0.1, 0.2, 0.3])
    vector_store.upsert(book_id, "current", [0.1, 0.2, 0.3], "当前版本", metadata)
    current = vector_store._collection(book_id)
    current.upsert(
        ids=["wrong-profile", "unknown-profile"],
        embeddings=[[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]],
        documents=["错误版本", "未知版本"],
        metadatas=[{**metadata, "embedding_profile": old_profile}, metadata],
    )
    assert vector_store.existing_ids(book_id) == {"current"}
    assert [
        hit["document"]
        for hit in vector_store.query(
            book_id,
            [0.1, 0.2, 0.3],
            source_type="chapter_summary",
            source_ids=["source"],
        )
    ] == ["当前版本"]
    assert not vector_store.query(book_id, [0.1, 0.2, 0.3], source_type="world")
    assert not vector_store.query(book_id, [0.1, 0.2, 0.3], source_ids=["other"])
    assert not vector_store.query(book_id, [0.1, 0.2, 0.3], source_ids=[])
    vector_store.delete_source(book_id, "chapter_summary", "source")
    assert not vector_store.existing_ids(book_id)
    assert legacy.count() == 1
    monkeypatch.setattr(vector_store, "get_embedding_profile", lambda: old_profile)
    assert vector_store.existing_ids(book_id) == {"old"}


def test_list_and_delete_books_across_profile_and_legacy_collections(
    chroma_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise more than one list page without creating hundreds of indexes.
    monkeypatch.setattr(vector_store, "_COLLECTION_PAGE_SIZE", 2)
    selected = "8a1b97e5-b1b1-4587-9dd4-a7d463dd4553"
    prefixed = f"{selected}_different"
    loose = "book_id__emb_not-a-digest"
    client = vector_store._client()
    legacy_names = [f"book_{book_id}" for book_id in (selected, prefixed, loose)]
    for name in [*legacy_names, "unowned_collection"]:
        client.create_collection(name, embedding_function=None)
    for profile in ("c" * 64, "d" * 64):
        monkeypatch.setattr(
            vector_store, "get_embedding_profile", lambda profile=profile: profile
        )
        vector_store.upsert(
            selected, "doc", [0.1, 0.2], "记忆", {"source_type": "world"}
        )

    assert sorted(vector_store.list_book_ids()) == sorted((selected, prefixed, loose))
    vector_store.delete_book(selected)
    vector_store.delete_book(selected)
    assert set(vector_store.list_book_ids()) == {prefixed, loose}
    assert {collection.name for collection in client.list_collections()} == {
        *legacy_names[1:],
        "unowned_collection",
    }


async def test_memory_audit_and_repair_reindex_current_profile_without_rewriting_prose(
    chroma_path: Path,
    client: httpx.AsyncClient,
    storage: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_profile, new_profile = "a" * 64, "b" * 64
    llm = FakeLLM()
    monkeypatch.setattr(sync, "get_llm", lambda: llm)
    monkeypatch.setattr(vector_store, "get_embedding_profile", lambda: old_profile)
    with storage() as db:
        book_id, _, chapter_id, _ = seed(db)
    body = "正文必须保留。"
    response = await client.put(f"/chapters/{chapter_id}/content", json={"body": body})
    assert response.status_code == 200
    await drain(storage, book_id)
    old_ids = vector_store.existing_ids(book_id)
    assert len(old_ids) == 1
    summary_calls = tuple(llm.chats)
    embedding_calls = len(llm.embeddings)
    status_url = f"/books/{book_id}/memory/status"
    repair_url = f"/books/{book_id}/memory/repair"
    assert (await client.get(status_url)).json()["missing"] == 0

    monkeypatch.setattr(vector_store, "get_embedding_profile", lambda: new_profile)
    status = (await client.get(status_url)).json()
    assert status["missing"] == 1 and status["pending"] == 0
    assert len(llm.embeddings) == embedding_calls
    assert (await client.post(repair_url)).json()["pending"] == 1
    await drain(storage, book_id)
    assert (await client.get(status_url)).json()["missing"] == 0
    assert tuple(llm.chats) == summary_calls
    assert len(llm.embeddings) == embedding_calls + 1
    assert vector_store.existing_ids(book_id).isdisjoint(old_ids)
    assert vector_store._collection(book_id, profile=old_profile).count() == 1
    with storage() as db:
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter and chapter.content and chapter.content.body == body
