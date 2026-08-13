from functools import lru_cache

import chromadb

from ..config import settings


@lru_cache
def _client() -> "chromadb.ClientAPI":
    return chromadb.PersistentClient(path=settings.chroma_dir)


def _collection(book_id: int):
    return _client().get_or_create_collection(name=f"book_{book_id}")


def upsert(
    book_id: int,
    doc_id: str,
    embedding: list[float],
    document: str,
    metadata: dict,
) -> None:
    if not embedding:
        return
    _collection(book_id).upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[document],
        metadatas=[metadata],
    )


def query(
    book_id: int,
    embedding: list[float],
    top_k: int = 5,
    where: dict | None = None,
) -> list[dict]:
    if not embedding:
        return []
    coll = _collection(book_id)
    if coll.count() == 0:
        return []
    res = coll.query(
        query_embeddings=[embedding],
        n_results=min(top_k, coll.count()),
        where=where,
    )
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    return [{"document": d, "metadata": m} for d, m in zip(docs, metas)]
