import json
import os
import sqlite3
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.api import ClientAPI

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
    return chromadb.PersistentClient(path=settings.chroma_dir)


def _collection(book_id: int):
    return _client().get_or_create_collection(name=f"book_{book_id}")


def upsert(
    book_id: int,
    doc_id: str,
    embedding: list[float],
    document: str,
    metadata: dict[str, Any],
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
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    return [{"document": d, "metadata": m} for d, m in zip(docs, metas)]
