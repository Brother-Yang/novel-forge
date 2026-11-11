"""从当前有效记忆构造正文上下文，不写入内容或同步任务。

token 以中文字符数粗略近似,足以做预算控制。
"""

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..llm import get_llm
from . import facts, sources, vector_store

logger = logging.getLogger(__name__)
MEMORY_DEGRADED_NOTE = (
    "部分记忆检索暂不可用，本次正文生成将继续。可在书籍工作区查看记忆状态。"
)
MEMORY_DEGRADED_BLOCK = "\n\n# 记忆状态\n" + MEMORY_DEGRADED_NOTE


def estimate_tokens(text: str) -> int:
    return len(text)


def _relevant_characters(
    db: Session, book_id: str, outline: str
) -> list[models.Character]:
    chars = list(
        db.scalars(
            select(models.Character)
            .where(models.Character.book_id == book_id)
            .order_by(models.Character.order_index)
        ).all()
    )
    mentioned = [c for c in chars if c.name and c.name in outline]
    # 若大纲未点名,则回退到前几位主要角色
    return mentioned or chars[:3]


def _previous_summaries(db: Session, chapter: models.Chapter) -> list[str]:
    rows = db.scalars(
        select(models.Chapter)
        .join(models.ChapterSummary)
        .where(
            models.Chapter.book_id == chapter.book_id,
            models.Chapter.order_index < chapter.order_index,
            models.Chapter.status == "done",
            models.ChapterSummary.source_hash != "",
        )
        .order_by(models.Chapter.order_index.desc())
        .limit(settings.previous_summary_count)
    ).all()
    summaries: list[str] = []
    for ch in reversed(rows):
        summary = sources.valid_summary(ch)
        if summary:
            summaries.append(f"[{ch.title}] {summary}")
    return summaries


def _history_source_ids(db: Session, chapter: models.Chapter) -> list[str]:
    indexed = select(models.MemorySyncJob.source_id).where(
        models.MemorySyncJob.book_id == chapter.book_id,
        models.MemorySyncJob.source_type == sources.CHAPTER,
        models.MemorySyncJob.status == "done",
        models.MemorySyncJob.source_hash != "",
    )
    return list(
        db.scalars(
            select(models.Chapter.id).where(
                models.Chapter.book_id == chapter.book_id,
                models.Chapter.order_index < chapter.order_index,
                models.Chapter.status == "done",
                models.Chapter.id.in_(indexed),
            )
        )
    )


def _volume_summary(db: Session, chapter: models.Chapter) -> str:
    if not chapter.volume_id:
        return ""
    volume = db.get(models.Volume, chapter.volume_id)
    job = sources.find_job(db, chapter.book_id, sources.VOLUME, chapter.volume_id)
    if not volume or not job or job.status != "done":
        return ""
    if job.source_hash != sources.source_hash(
        db, chapter.book_id, sources.VOLUME, volume.id
    ):
        return ""
    completed = db.scalars(
        select(models.Chapter).where(
            models.Chapter.volume_id == volume.id,
            models.Chapter.status == "done",
            models.Chapter.book_id == chapter.book_id,
        )
    ).all()
    # A rolling volume summary can include later events. Never use it to rewrite
    # an earlier chapter, or use the initial outline as if it were history.
    if not completed or any(ch.order_index >= chapter.order_index for ch in completed):
        return ""
    if not any(sources.valid_summary(ch) for ch in completed):
        return ""
    return volume.memory_summary


def _valid_hits(
    db: Session, chapter: models.Chapter, hits: list[dict[str, Any]], source_type: str
) -> list[str]:
    documents: list[str] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        source_id = meta.get("source_id")
        if not isinstance(source_id, str) or meta.get("source_type") != source_type:
            continue
        job = sources.find_job(db, chapter.book_id, source_type, source_id)
        if not job or job.status != "done" or not job.source_hash:
            continue
        if (
            meta.get("revision") != job.revision
            or meta.get("source_hash") != job.source_hash
        ):
            continue
        if source_type == sources.CHAPTER:
            source = db.get(models.Chapter, source_id)
            if (
                not source
                or source.book_id != chapter.book_id
                or source.status != "done"
                or source.order_index >= chapter.order_index
                or not sources.valid_summary(source)
            ):
                continue
        else:
            source = db.get(models.Worldbuilding, source_id)
            if not source or source.book_id != chapter.book_id:
                continue
        if (
            sources.source_hash(db, chapter.book_id, source_type, source_id)
            != job.source_hash
        ):
            continue
        document = hit.get("document")
        if isinstance(document, str):
            documents.append(document)
    return documents


async def build_chapter_context(db: Session, chapter: models.Chapter) -> str:
    """在 token 预算内拼装本章生成所需上下文。"""
    budget = settings.context_token_budget
    book = db.get(models.Book, chapter.book_id)
    parts: list[str] = []

    if book:
        idea = (book.brief or book.intro or "").strip()
        intro = (book.intro or "").strip()
        identity = f"# 作品信息\n《{book.title}》 题材:{book.genre} 基调:{book.tone}"
        if idea:
            identity += f"\n创作想法:{idea}"
        if intro:
            identity += f"\n简介:{intro}"
        parts.append(identity)
    parts.append(f"# 本章大纲\n{chapter.outline or '(无,请根据前文自然推进)'}")

    # 相关人物卡
    earlier_facts = facts.replay(db, chapter.book_id, before_order=chapter.order_index)
    chars = _relevant_characters(db, chapter.book_id, chapter.outline or "")
    if chars:
        blocks: list[str] = []
        for c in chars:
            state = earlier_facts.characters.get(c.id)
            block = f"## {c.name}\n{c.md_content}"
            if state:
                block += "\n前章确立的人物状态:" + json.dumps(state, ensure_ascii=False)
            blocks.append(block)
        parts.append("# 相关人物\n" + "\n\n".join(blocks))

    # 相关剧情线程
    threads = db.scalars(
        select(models.PlotThread).where(
            models.PlotThread.book_id == chapter.book_id,
        )
    ).all()
    active_threads = [
        (t, earlier_facts.threads.get(t.id, "open"))
        for t in threads
        if earlier_facts.threads.get(t.id) != "closed"
    ]
    if active_threads:
        parts.append(
            "# 待推进/呼应的剧情线程\n"
            + "\n".join(
                f"- ({status}) {t.name}: {t.description}"
                for t, status in active_threads
            )
        )

    # 向量召回:世界观 + 历史相关片段。检索失败不阻断正文生成。
    degraded = False
    operation = "embed"
    book_id, chapter_id = chapter.book_id, chapter.id
    query_text = chapter.outline or chapter.title
    db.rollback()
    try:
        llm = get_llm()
        query_emb = await llm.embed(query_text)
        if not query_emb:
            raise ValueError("empty query embedding")
        # An edit can commit while the embedding request is awaiting the model.
        db.expire_all()
        operation = "worldbuilding_query"
        wb_hits = vector_store.query(
            chapter.book_id, query_emb, top_k=12, where={"source_type": sources.WORLD}
        )
        world_documents = _valid_hits(db, chapter, wb_hits, sources.WORLD)[:4]
        if world_documents:
            parts.append("# 相关设定(检索)\n" + "\n".join(world_documents))
        operation = "chapter_query"
        # Current SQL order determines eligibility before ranking. Reordering
        # chapters changes the very next recall without a model re-embedding.
        if history_ids := _history_source_ids(db, chapter):
            sum_hits = vector_store.query(
                chapter.book_id,
                query_emb,
                top_k=12,
                where={
                    "$and": [
                        {"source_type": sources.CHAPTER},
                        {"source_id": {"$in": history_ids}},
                    ]
                },
            )
            history_documents = _valid_hits(db, chapter, sum_hits, sources.CHAPTER)[:4]
            if history_documents:
                parts.append("# 相关历史情节(检索)\n" + "\n".join(history_documents))
    except Exception as exc:  # noqa: BLE001 - provider/Chroma errors allow SQL fallback
        logger.warning(
            "RAG degraded book_id=%s chapter_id=%s operation=%s error_type=%s",
            book_id,
            chapter_id,
            operation,
            type(exc).__name__,
        )
        degraded = True

    # 前情摘要;超预算则降级为卷摘要
    db.expire_all()
    prev = _previous_summaries(db, chapter)
    current = "\n\n".join(parts)
    prev_block = "# 前情提要\n" + "\n".join(prev) if prev else ""
    if prev and estimate_tokens(current + prev_block) > budget:
        vol_sum = _volume_summary(db, chapter)
        prev_block = f"# 前情提要(卷级摘要)\n{vol_sum}" if vol_sum else prev_block
    if prev_block:
        parts.append(prev_block)

    context = "\n\n".join(parts)
    # 最终兜底裁剪
    if estimate_tokens(context) > budget * 1.5:
        context = context[: int(budget * 1.5)]
    if degraded:
        context += MEMORY_DEGRADED_BLOCK
    return context
