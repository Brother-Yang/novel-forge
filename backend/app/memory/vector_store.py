import json
import os
import sqlite3
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from ..config import settings


def _repair_collection_config(path: str) -> None:
    """补齐旧版/跨版本 Chroma 写入的空 config_json_str,避免 KeyError: '_type'。"""
    db_path = os.path.join(path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(collections)")}
        if "config_json_str" not in cols:
            return
        for cid, cfg in conn.execute("SELECT id, config_json_str FROM collections"):
            try:
                obj = json.loads(cfg or "{}")
            except json.JSONDecodeError:
                obj = {}
            if not isinstance(obj, dict):
                obj = {}
            if obj.get("_type"):
                continue
            obj["_type"] = "CollectionConfigurationInternal"
            _ = conn.execute(
                "UPDATE collections SET config_json_str = ? WHERE id = ?",
                (json.dumps(obj), cid),
            )
        conn.commit()
    finally:
        conn.close()


@lru_cache
def _client() -> ClientAPI:
    _repair_collection_config(settings.chroma_dir)
    return chromadb.PersistentClient(
        path=settings.chroma_dir,
        settings=ChromaSettings(chroma_api_impl="chromadb.api.rust.RustBindingsAPI"),
    )


def _collection(book_id: str, *, create: bool = False) -> Collection:
    # Always supply numerical vectors to Chroma. Opening a legacy collection
    # with None does not erase its saved embedding configuration.
    client = _client()
    name = f"book_{book_id}"
    if create:
        return client.get_or_create_collection(name=name, embedding_function=None)
    return client.get_collection(name=name, embedding_function=None)


def upsert(
    book_id: str,
    doc_id: str,
    embedding: list[float],
    document: str,
    metadata: dict[str, Any],
) -> None:
    if not embedding:
        return
    _collection(book_id, create=True).upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[document],
        metadatas=[metadata],
    )


def query(
    book_id: str,
    embedding: list[float],
    top_k: int = 5,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not embedding:
        return []
    try:
        coll = _collection(book_id)
    except NotFoundError:
        return []
    count = coll.count()
    if count == 0:
        return []
    res = coll.query(
        query_embeddings=[embedding],
        n_results=min(top_k, count),
        where=where,
    )
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    return [{"document": d, "metadata": m} for d, m in zip(docs, metas)]


def delete_source(book_id: str, source_type: str, source_id: str) -> None:
    try:
        coll = _collection(book_id)
    except NotFoundError:
        return
    coll.delete(
        where={
            "$and": [
                {"source_type": source_type},
                {"source_id": source_id},
            ]
        }
    )


def list_book_ids() -> list[str]:
    return [
        collection.name.removeprefix("book_")
        for collection in _client().list_collections()
        if collection.name.startswith("book_")
    ]


def existing_ids(book_id: str) -> set[str]:
    try:
        coll = _collection(book_id)
    except NotFoundError:
        return set()
    return set(coll.get(include=[])["ids"])


def delete_book(book_id: str) -> None:
    """Delete all memory for a book; a missing collection is already clean."""
    try:
        _client().delete_collection(name=f"book_{book_id}")
    except NotFoundError:
        return
