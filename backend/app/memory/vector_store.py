import json
import os
import re
import sqlite3
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.api.types import Where
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from ..config import settings
from ..llm import get_embedding_profile

_BOOK_PREFIX = "book_"
_PROFILE_SUFFIX = "__emb_"
_PROFILE_PATTERN = re.compile(r"[0-9a-f]{64}")
_PROFILE_FIELD = "embedding_profile"
_COLLECTION_PAGE_SIZE = 100


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


def _collection(
    book_id: str, *, create: bool = False, profile: str | None = None
) -> Collection:
    # Chroma fixes the vector dimension per collection. Different embedding
    # profiles need distinct collections even if they happen to share dimensions.
    client = _client()
    profile = profile if profile is not None else get_embedding_profile()
    name = f"{_BOOK_PREFIX}{book_id}{_PROFILE_SUFFIX}{profile}"
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
    profile = get_embedding_profile()
    _collection(book_id, create=True, profile=profile).upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[document],
        metadatas=[{**metadata, _PROFILE_FIELD: profile}],
    )


def query(
    book_id: str,
    embedding: list[float],
    top_k: int = 5,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not embedding:
        return []
    profile = get_embedding_profile()
    try:
        coll = _collection(book_id, profile=profile)
    except NotFoundError:
        return []
    count = coll.count()
    if count == 0:
        return []
    profile_filter: Where = {_PROFILE_FIELD: profile}
    res = coll.query(
        query_embeddings=[embedding],
        n_results=min(top_k, count),
        where={"$and": [where, profile_filter]} if where else profile_filter,
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


def _book_collections(client: ClientAPI) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    offset = 0
    while True:
        page = client.list_collections(limit=_COLLECTION_PAGE_SIZE, offset=offset)
        for collection in page:
            name = collection.name
            if name.startswith(_BOOK_PREFIX):
                legacy_id = name.removeprefix(_BOOK_PREFIX)
                book_id, separator, profile = legacy_id.rpartition(_PROFILE_SUFFIX)
                if not separator or not _PROFILE_PATTERN.fullmatch(profile):
                    book_id = legacy_id
                names.append((book_id, name))
        if len(page) < _COLLECTION_PAGE_SIZE:
            return names
        offset += len(page)


def list_book_ids() -> list[str]:
    return list(dict.fromkeys(book_id for book_id, _ in _book_collections(_client())))


def existing_ids(book_id: str) -> set[str]:
    profile = get_embedding_profile()
    try:
        coll = _collection(book_id, profile=profile)
    except NotFoundError:
        return set()
    return set(coll.get(where={_PROFILE_FIELD: profile}, include=[])["ids"])


def delete_book(book_id: str) -> None:
    """Delete every embedding profile and legacy collection owned by a book."""
    client = _client()
    for owner_id, name in _book_collections(client):
        if owner_id == book_id:
            try:
                client.delete_collection(name=name)
            except NotFoundError:
                continue
