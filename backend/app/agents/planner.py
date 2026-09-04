"""把一段话规划成书目元数据与生成规模,供全书编排器使用。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from .. import models
from ..llm import get_llm
from . import prompts

WORDS_PER_CHAPTER_MIN = 2000
WORDS_PER_CHAPTER_MAX = 4000
VOLUME_MIN = 1
VOLUME_MAX = 12
CHAPTERS_PER_VOLUME_MAX = 20
CHARACTER_MIN = 3
CHARACTER_MAX = 8

_SCALE_KEYS = ("volume_count", "chapters_per_volume", "words_per_chapter")


@dataclass
class NovelPlan:
    title: str
    genre: str
    tone: str
    intro: str
    volume_count: int
    chapters_per_volume: int
    words_per_chapter: int
    character_count: int

    def to_checkpoint(self) -> dict[str, int]:
        return {
            "volume_count": self.volume_count,
            "chapters_per_volume": self.chapters_per_volume,
            "words_per_chapter": self.words_per_chapter,
            "character_count": self.character_count,
        }


def extract_json_object(text: str) -> dict[str, object]:
    """从模型输出中抽出第一个 JSON 对象,容忍 Markdown 围栏。"""
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    blob = fenced.group(1) if fenced else stripped
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("规划结果中没有 JSON 对象")
    parsed = json.loads(blob[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("规划结果不是 JSON 对象")
    return parsed


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def _as_str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def clamp_plan(
    raw: dict[str, object],
    *,
    target_words: int,
    title: str = "",
    genre: str = "",
    tone: str = "",
    brief: str = "",
) -> NovelPlan:
    """用目标字数约束模型给出的规模,用户已填字段优先。"""
    words = _clamp(
        _as_int(raw.get("words_per_chapter"), 2500),
        WORDS_PER_CHAPTER_MIN,
        WORDS_PER_CHAPTER_MAX,
    )
    target = max(1, int(target_words))
    total_from_target = max(1, target // words)

    raw_vols = raw.get("volume_count")
    raw_cpv = raw.get("chapters_per_volume")
    vols: int | None = None
    cpv: int | None = None
    if raw_vols is not None and raw_cpv is not None:
        candidate_vols = _clamp(_as_int(raw_vols, 0), VOLUME_MIN, VOLUME_MAX)
        candidate_cpv = _clamp(_as_int(raw_cpv, 0), 1, CHAPTERS_PER_VOLUME_MAX)
        planned_words = candidate_vols * candidate_cpv * words
        if target * 0.5 <= planned_words <= target * 2.0 and candidate_vols > 0:
            vols = candidate_vols
            cpv = candidate_cpv

    if vols is None or cpv is None:
        suggested_vols = max(VOLUME_MIN, round(total_from_target / 12))
        vols = _clamp(_as_int(raw_vols, suggested_vols), VOLUME_MIN, VOLUME_MAX)
        vols = min(vols, total_from_target)
        cpv = min(CHAPTERS_PER_VOLUME_MAX, max(1, math.ceil(total_from_target / vols)))

    characters = _clamp(_as_int(raw.get("character_count"), 5), CHARACTER_MIN, CHARACTER_MAX)
    user_title = "" if is_placeholder_title(title, brief) else title
    planned_title = user_title or _as_str(raw.get("title")) or "未命名"
    planned_genre = genre or _as_str(raw.get("genre"))
    planned_tone = tone or _as_str(raw.get("tone"))
    planned_intro = _as_str(raw.get("intro"))
    return NovelPlan(
        title=planned_title,
        genre=planned_genre,
        tone=planned_tone,
        intro=planned_intro,
        volume_count=vols,
        chapters_per_volume=cpv,
        words_per_chapter=words,
        character_count=characters,
    )


def plan_from_checkpoint(
    ckpt: dict[str, object],
    *,
    title: str,
    genre: str,
    tone: str,
    intro: str,
) -> NovelPlan | None:
    if not all(k in ckpt for k in _SCALE_KEYS):
        return None
    return NovelPlan(
        title=title,
        genre=genre,
        tone=tone,
        intro=intro,
        volume_count=_clamp(_as_int(ckpt.get("volume_count"), 1), VOLUME_MIN, VOLUME_MAX),
        chapters_per_volume=_clamp(
            _as_int(ckpt.get("chapters_per_volume"), 1), 1, CHAPTERS_PER_VOLUME_MAX
        ),
        words_per_chapter=_clamp(
            _as_int(ckpt.get("words_per_chapter"), 2500),
            WORDS_PER_CHAPTER_MIN,
            WORDS_PER_CHAPTER_MAX,
        ),
        character_count=_clamp(
            _as_int(ckpt.get("character_count"), 5), CHARACTER_MIN, CHARACTER_MAX
        ),
    )


def fallback_title(brief: str) -> str:
    line = brief.strip().splitlines()[0].strip() if brief.strip() else ""
    return (line[:40] if line else "未命名")


def is_placeholder_title(title: str, brief: str = "") -> bool:
    """用户没起名时的占位书名,包括把创作想法截成书名的历史写法。"""
    text = (title or "").strip()
    if not text or text == "未命名":
        return True
    idea = (brief or "").strip()
    return bool(idea) and text == fallback_title(idea)


def apply_plan_to_book(book: models.Book, plan: NovelPlan) -> None:
    """规划结果写回书籍;用户已填的书名/题材/基调/简介不覆盖。"""
    if is_placeholder_title(book.title, book.brief):
        book.title = plan.title
    if not book.genre:
        book.genre = plan.genre
    if not book.tone:
        book.tone = plan.tone
    if not book.intro:
        book.intro = plan.intro


async def plan_novel(
    brief: str,
    *,
    title: str = "",
    genre: str = "",
    tone: str = "",
    target_words: int = 100000,
) -> NovelPlan:
    llm = get_llm()

    def _safe(value: str) -> str:
        return value.replace("{", "{{").replace("}", "}}")

    user = prompts.PLANNER_USER.format(
        brief=_safe(brief),
        title=_safe(title or "由你拟定"),
        genre=_safe(genre or "由你拟定"),
        tone=_safe(tone or "由你拟定"),
        target_words=target_words,
    )
    try:
        text = await llm.chat(user, system=prompts.PLANNER_SYSTEM)
        raw = extract_json_object(text)
    except (ValueError, json.JSONDecodeError, KeyError):
        raw = {}
    return clamp_plan(
        raw,
        target_words=target_words,
        title=title,
        genre=genre,
        tone=tone,
        brief=brief,
    )
