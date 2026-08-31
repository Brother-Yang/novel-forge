"""分层生成 Agent。

每个函数是异步生成器,产出 (event, data) 元组,便于 SSE 转发:
  - token: 流式文本片段
  - log:   状态提示
  - done:  完成
  - error: 出错
生成结束后负责把结构化结果落库(卷/章/人物/设定/摘要)。
"""

import re
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..llm import get_llm
from ..memory import context as memory
from . import prompts

Event = tuple[str, str]


def _split_h2(text: str) -> list[str]:
    blocks = re.split(r"(?m)^##[ \t]+", text)
    return [b.strip() for b in blocks[1:] if b.strip()]


def _split_h1(text: str) -> list[str]:
    blocks = re.split(r"(?m)^#[ \t]+", text)
    return [b.strip() for b in blocks[1:] if b.strip()]


async def _stream(system: str, user: str) -> AsyncIterator[Event]:
    llm = get_llm()
    full = []
    async for chunk in llm.chat_stream(user, system=system):
        full.append(chunk)
        yield ("token", chunk)
    yield ("full", "".join(full))


# ---------- 1. 世界观 ----------
async def gen_worldbuilding(db: Session, book_id: int) -> AsyncIterator[Event]:
    book = db.get(models.Book, book_id)
    user = prompts.WORLDBUILDING_USER.format(
        title=book.title, genre=book.genre, tone=book.tone, intro=book.intro
    )
    full = ""
    async for ev, data in _stream(prompts.WORLDBUILDING_SYSTEM, user):
        if ev == "full":
            full = data
        else:
            yield ev, data

    count = 0
    for block in _split_h2(full):
        lines = block.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        item = models.Worldbuilding(
            book_id=book_id, category="general", title=title, md_content=body
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        try:
            await memory.index_worldbuilding(db, item)
        except Exception as exc:  # 索引失败不阻断设定落库
            yield ("log", f"设定《{title}》已保存,索引失败:{exc}")
        count += 1
    yield ("log", f"已生成并索引 {count} 条世界观设定")
    yield ("done", "")


# ---------- 2. 人物 ----------
def _world_digest(db: Session, book_id: int, limit: int = 1500) -> str:
    items = db.scalars(
        select(models.Worldbuilding).where(models.Worldbuilding.book_id == book_id)
    ).all()
    text = "\n".join(f"{i.title}: {i.md_content}" for i in items)
    return text[:limit]


async def gen_characters(
    db: Session, book_id: int, count: int = 5
) -> AsyncIterator[Event]:
    book = db.get(models.Book, book_id)
    user = prompts.CHARACTERS_USER.format(
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        world=_world_digest(db, book_id),
        count=count,
    )
    full = ""
    async for ev, data in _stream(prompts.CHARACTERS_SYSTEM, user):
        if ev == "full":
            full = data
        else:
            yield ev, data

    base = db.scalar(
        select(models.Character.order_index)
        .where(models.Character.book_id == book_id)
        .order_by(models.Character.order_index.desc())
    )
    order = (base or 0) + 1
    created = 0
    for block in _split_h1(full):
        name = block.split("\n", 1)[0].strip()
        if not name:
            continue
        db.add(
            models.Character(
                book_id=book_id,
                name=name,
                order_index=order,
                md_content="# " + block,
                state_json="{}",
            )
        )
        order += 1
        created += 1
    db.commit()
    yield ("log", f"已生成 {created} 个人物(各一份 md)")
    yield ("done", "")


# ---------- 3. 全书大纲 + 剧情线程 ----------
def _character_digest(db: Session, book_id: int, limit: int = 1200) -> str:
    chars = db.scalars(
        select(models.Character).where(models.Character.book_id == book_id)
    ).all()
    return "\n".join(f"- {c.name}" for c in chars)[:limit]


async def gen_outline(
    db: Session, book_id: int, volume_count: int = 3
) -> AsyncIterator[Event]:
    book = db.get(models.Book, book_id)
    user = prompts.OUTLINE_USER.format(
        title=book.title,
        genre=book.genre,
        tone=book.tone,
        target_words=book.target_words,
        world=_world_digest(db, book_id),
        characters=_character_digest(db, book_id),
        volume_count=volume_count,
    )
    full = ""
    async for ev, data in _stream(prompts.OUTLINE_SYSTEM, user):
        if ev == "full":
            full = data
        else:
            yield ev, data

    # 拆出剧情线程段("# 剧情线程")
    thread_text = ""
    volume_text = full
    m = re.search(r"(?m)^#[ \t]+剧情线程", full)
    if m:
        volume_text = full[: m.start()]
        thread_text = full[m.end():]

    base = db.scalar(
        select(models.Volume.order_index)
        .where(models.Volume.book_id == book_id)
        .order_by(models.Volume.order_index.desc())
    )
    order = (base or -1) + 1
    vcount = 0
    for block in _split_h2(volume_text):
        lines = block.split("\n", 1)
        title = lines[0].strip()
        summary = lines[1].strip() if len(lines) > 1 else ""
        db.add(
            models.Volume(
                book_id=book_id, title=title, order_index=order, summary=summary
            )
        )
        order += 1
        vcount += 1
    db.commit()

    tcount = 0
    for line in thread_text.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        if "|" in line:
            name, desc = line.split("|", 1)
        else:
            name, desc = line, ""
        db.add(
            models.PlotThread(
                book_id=book_id,
                name=name.strip(),
                description=desc.strip(),
                status="open",
            )
        )
        tcount += 1
    db.commit()
    yield ("log", f"已生成 {vcount} 卷、{tcount} 条剧情线程")
    yield ("done", "")


# ---------- 4. 章节大纲 ----------
async def gen_chapter_outlines(
    db: Session, volume_id: int, chapter_count: int = 10
) -> AsyncIterator[Event]:
    volume = db.get(models.Volume, volume_id)
    if not volume:
        yield ("error", "卷不存在")
        return
    book = db.get(models.Book, volume.book_id)
    user = prompts.CHAPTER_OUTLINE_USER.format(
        title=book.title,
        genre=book.genre,
        volume_title=volume.title,
        volume_summary=volume.summary,
        chapter_count=chapter_count,
    )
    full = ""
    async for ev, data in _stream(prompts.CHAPTER_OUTLINE_SYSTEM, user):
        if ev == "full":
            full = data
        else:
            yield ev, data

    base = db.scalar(
        select(models.Chapter.order_index)
        .where(models.Chapter.book_id == book.id)
        .order_by(models.Chapter.order_index.desc())
    )
    order = (base or -1) + 1
    created = 0
    for block in _split_h2(full):
        lines = block.split("\n", 1)
        title = lines[0].strip()
        outline = lines[1].strip() if len(lines) > 1 else ""
        db.add(
            models.Chapter(
                book_id=book.id,
                volume_id=volume_id,
                title=title,
                order_index=order,
                outline=outline,
                status="outline",
            )
        )
        order += 1
        created += 1
    db.commit()
    yield ("log", f"已为《{volume.title}》生成 {created} 章大纲")
    yield ("done", "")


# ---------- 5+6. 章节正文 + 一致性回写 ----------
async def gen_chapter_body(
    db: Session, chapter_id: int, words: int = 2000
) -> AsyncIterator[Event]:
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter:
        yield ("error", "章节不存在")
        return
    book = db.get(models.Book, chapter.book_id)
    chapter.status = "drafting"
    db.commit()

    ctx = await memory.build_chapter_context(db, chapter)
    user = prompts.CHAPTER_BODY_USER.format(
        title=book.title, chapter_title=chapter.title, context=ctx, words=words
    )

    full = ""
    async for ev, data in _stream(prompts.CHAPTER_BODY_SYSTEM, user):
        if ev == "full":
            full = data
        else:
            yield ev, data

    body = full.strip()
    if chapter.content:
        chapter.content.body = body
    else:
        db.add(models.ChapterContent(chapter_id=chapter.id, body=body))
    chapter.word_count = len(body)
    chapter.status = "done"
    db.commit()

    # 一致性回写:生成章摘要并写入记忆库
    yield ("log", "正在提取章摘要并回写记忆...")
    llm = get_llm()
    try:
        summary = await llm.chat(
            prompts.SUMMARY_USER.format(body=body[:4000]),
            system=prompts.SUMMARY_SYSTEM,
        )
        await memory.index_chapter_summary(db, chapter, summary.strip())
    except Exception as exc:  # 摘要失败不影响正文
        yield ("log", f"摘要生成失败(已跳过):{exc}")

    yield ("done", "")
