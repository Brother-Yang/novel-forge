"""RAG 记忆:章摘要索引 + 正文上下文构造(带 token 预算)。

token 以中文字符数粗略近似,足以做预算控制。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..llm import get_llm
from . import vector_store


def estimate_tokens(text: str) -> int:
    return len(text)


async def index_worldbuilding(db: Session, item: models.Worldbuilding) -> None:
    llm = get_llm()
    text = f"{item.title}\n{item.md_content}"
    emb = await llm.embed(text)
    doc_id = f"wb-{item.id}"
    vector_store.upsert(
        item.book_id,
        doc_id,
        emb,
        text,
        {"source_type": "worldbuilding", "source_id": item.id, "title": item.title},
    )
    _record_ref(db, item.book_id, "worldbuilding", item.id, doc_id)


async def index_chapter_summary(
    db: Session, chapter: models.Chapter, summary: str
) -> None:
    """保存章摘要到 SQLite 并写入向量库,供后续 RAG 召回。"""
    existing = db.get(models.ChapterSummary, chapter.id)
    if existing:
        existing.summary = summary
    else:
        db.add(models.ChapterSummary(chapter_id=chapter.id, summary=summary))
    db.commit()

    llm = get_llm()
    emb = await llm.embed(summary)
    doc_id = f"chsum-{chapter.id}"
    vector_store.upsert(
        chapter.book_id,
        doc_id,
        emb,
        summary,
        {
            "source_type": "chapter_summary",
            "source_id": chapter.id,
            "order_index": chapter.order_index,
        },
    )
    _record_ref(db, chapter.book_id, "chapter_summary", chapter.id, doc_id)


def _record_ref(
    db: Session, book_id: str, source_type: str, source_id: int, chroma_id: str
) -> None:
    exists = db.scalar(
        select(models.EmbeddingRef).where(
            models.EmbeddingRef.source_type == source_type,
            models.EmbeddingRef.source_id == source_id,
        )
    )
    if not exists:
        db.add(
            models.EmbeddingRef(
                book_id=book_id,
                source_type=source_type,
                source_id=source_id,
                chroma_id=chroma_id,
            )
        )
        db.commit()


def _relevant_characters(db: Session, book_id: str, outline: str) -> list[models.Character]:
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
        .where(
            models.Chapter.book_id == chapter.book_id,
            models.Chapter.order_index < chapter.order_index,
        )
        .order_by(models.Chapter.order_index.desc())
        .limit(settings.previous_summary_count)
    ).all()
    summaries: list[str] = []
    for ch in reversed(rows):
        s = db.get(models.ChapterSummary, ch.id)
        if s and s.summary:
            summaries.append(f"[{ch.title}] {s.summary}")
    return summaries


def _volume_summary(db: Session, chapter: models.Chapter) -> str:
    if not chapter.volume_id:
        return ""
    vol = db.get(models.Volume, chapter.volume_id)
    return vol.summary if vol else ""


async def build_chapter_context(db: Session, chapter: models.Chapter) -> str:
    """在 token 预算内拼装本章生成所需上下文。"""
    budget = settings.context_token_budget
    book = db.get(models.Book, chapter.book_id)
    parts: list[str] = []

    if book:
        parts.append(f"# 作品信息\n《{book.title}》 题材:{book.genre} 基调:{book.tone}")
    parts.append(f"# 本章大纲\n{chapter.outline or '(无,请根据前文自然推进)'}")

    # 相关人物卡
    chars = _relevant_characters(db, chapter.book_id, chapter.outline or "")
    if chars:
        blocks: list[str] = []
        for c in chars:
            blocks.append(f"## {c.name}\n{c.md_content}\n当前状态:{c.state_json}")
        parts.append("# 相关人物\n" + "\n\n".join(blocks))

    # 相关剧情线程
    threads = db.scalars(
        select(models.PlotThread).where(
            models.PlotThread.book_id == chapter.book_id,
            models.PlotThread.status != "closed",
        )
    ).all()
    if threads:
        parts.append(
            "# 待推进/呼应的剧情线程\n"
            + "\n".join(f"- ({t.status}) {t.name}: {t.description}" for t in threads)
        )

    # 向量召回:世界观 + 历史相关片段。检索失败不阻断正文生成。
    try:
        llm = get_llm()
        query_emb = await llm.embed(chapter.outline or chapter.title)
        wb_hits = vector_store.query(
            chapter.book_id, query_emb, top_k=4, where={"source_type": "worldbuilding"}
        )
        if wb_hits:
            parts.append(
                "# 相关设定(检索)\n" + "\n".join(h["document"] for h in wb_hits)
            )
        sum_hits = vector_store.query(
            chapter.book_id, query_emb, top_k=4, where={"source_type": "chapter_summary"}
        )
        if sum_hits:
            parts.append(
                "# 相关历史情节(检索)\n" + "\n".join(h["document"] for h in sum_hits)
            )
    except Exception:
        pass

    # 前情摘要;超预算则降级为卷摘要
    prev = _previous_summaries(db, chapter)
    current = "\n\n".join(parts)
    prev_block = "# 前情提要\n" + "\n".join(prev) if prev else ""
    if prev and estimate_tokens(current + prev_block) > budget:
        vol_sum = _volume_summary(db, chapter)
        prev_block = f"# 前情提要(卷级摘要)\n{vol_sum}" if vol_sum else ""
    if prev_block:
        parts.append(prev_block)

    context = "\n\n".join(parts)
    # 最终兜底裁剪
    if estimate_tokens(context) > budget * 1.5:
        context = context[: int(budget * 1.5)]
    return context
