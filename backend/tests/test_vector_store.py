"""Exercise the local Chroma adapter with numeric vectors and no model access."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from chromadb.api.types import DefaultEmbeddingFunction

from app.memory import vector_store


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
        {"document": "正文", "metadata": {"source_type": "chapter"}},
    ]
    collection = vector_store._collection(book_id)
    assert collection.configuration_json["embedding_function"] is None


def test_numeric_operations_reuse_existing_collection_without_running_default_model(
    chroma_path: Path,
) -> None:
    book_id = "legacy-book"
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

    assert vector_store.existing_ids(book_id) == {"existing"}
    assert vector_store.query(book_id, [0.1, 0.2])[0]["document"] == "旧正文"
    vector_store.upsert(book_id, "new", [0.3, 0.4], "新正文", metadata)
    assert vector_store.existing_ids(book_id) == {"existing", "new"}
    vector_store.delete_source(book_id, "chapter_summary", "source")
    assert not vector_store.existing_ids(book_id)
