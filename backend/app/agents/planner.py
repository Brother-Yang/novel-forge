"""把一段话规划成书目元数据与生成规模,供全书编排器使用。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import cast

from .. import models, schemas
from ..llm import get_llm
from . import prompts, stages
from .errors import GenerationError

WORDS_PER_CHAPTER_MIN = schemas.NOVEL_WORDS_PER_CHAPTER_MIN
WORDS_PER_CHAPTER_MAX = schemas.NOVEL_WORDS_PER_CHAPTER_MAX
VOLUME_MIN = 1
VOLUME_MAX = schemas.NOVEL_VOLUME_MAX
CHAPTERS_PER_VOLUME_MAX = schemas.NOVEL_CHAPTERS_PER_VOLUME_MAX
TARGET_WORDS_MAX = schemas.NOVEL_TARGET_WORDS_MAX
CHARACTER_MIN = 3
CHARACTER_MAX = 8
KEY_ELEMENTS_MAX = 10

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
    key_elements: list[str]

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
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    blob = fenced.group(1) if fenced else stripped
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("规划结果中没有 JSON 对象")
    parsed = cast(object, json.loads(blob[start : end + 1]))
    obj = _as_object_dict(parsed)
    if obj is None:
        raise ValueError("规划结果不是 JSON 对象")
    return obj


def _as_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def _as_str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _parse_key_elements(raw: object) -> list[str]:
    """从模型输出里提取用户已明确给出的要素列表,过滤空白项并限长。"""
    if not isinstance(raw, list):
        return []
    items = [_as_str(item) for item in cast(list[object], raw)]
    return [item for item in items if item][:KEY_ELEMENTS_MAX]


def format_key_elements(key_elements_json: str) -> str:
    """把 Book.key_elements 的 JSON 字符串格式化成 prompt 里可读的多行文本。"""
    lines = _parse_key_elements(_safe_json_list(key_elements_json))
    if not lines:
        return "(无,由你自由创作)"
    return "\n".join(f"- {line}" for line in lines)


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def validate_target_words(target_words: int) -> None:
    """容量检查必须在调用模型前完成，不能以截断计划来接受超限目标。"""
    if not 1 <= target_words <= TARGET_WORDS_MAX:
        raise GenerationError(
            f"目标字数 {target_words} 超出当前规划范围 1..{TARGET_WORDS_MAX}；"
            f"最多 {VOLUME_MAX} 卷、每卷 {CHAPTERS_PER_VOLUME_MAX} 章、"
            f"每章 {WORDS_PER_CHAPTER_MAX} 字；实际生成字数仍取决于模型输出"
        )


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
    validate_target_words(target_words)
    # 优先扩展卷数，达到最大卷数后才提高每章规划字数，避免单次生成几百章细纲。
    words = max(words, _ceil_div(target_words, VOLUME_MAX * CHAPTERS_PER_VOLUME_MAX))
    total_chapters = _ceil_div(target_words, words)
    min_volumes = _ceil_div(total_chapters, CHAPTERS_PER_VOLUME_MAX)
    preferred_volumes = _clamp(
        _as_int(raw.get("volume_count"), min_volumes), min_volumes, VOLUME_MAX
    )
    candidate_volumes = _as_int(raw.get("volume_count"), 0)
    candidate_chapters = _as_int(raw.get("chapters_per_volume"), 0)
    candidate_words = candidate_volumes * candidate_chapters * words
    if (
        VOLUME_MIN <= candidate_volumes <= VOLUME_MAX
        and 1 <= candidate_chapters <= CHAPTERS_PER_VOLUME_MAX
        and target_words
        <= candidate_words
        <= target_words + max(words, target_words // 20)
    ):
        vols, cpv = candidate_volumes, candidate_chapters
    else:
        # 将规模误差限制在一章或 5% 内，再靠近建议卷数，避免为凑整数把短篇拆成几十卷。
        choices = [
            (volume_count, _ceil_div(total_chapters, volume_count))
            for volume_count in range(min_volumes, min(VOLUME_MAX, total_chapters) + 1)
            if volume_count * _ceil_div(total_chapters, volume_count) * words
            <= target_words + max(words, target_words // 20)
        ]
        vols, cpv = min(
            choices,
            key=lambda scale: (
                abs(scale[0] - preferred_volumes),
                scale[0] * scale[1] - total_chapters,
                scale[0],
            ),
        )

    characters = _clamp(
        _as_int(raw.get("character_count"), 5), CHARACTER_MIN, CHARACTER_MAX
    )
    user_title = "" if is_placeholder_title(title, brief) else title
    planned_title = user_title or _as_str(raw.get("title")) or "未命名"
    planned_genre = genre or _as_str(raw.get("genre"))
    planned_tone = tone or _as_str(raw.get("tone"))
    planned_intro = _as_str(raw.get("intro"))
    key_elements = _parse_key_elements(raw.get("key_elements"))
    return NovelPlan(
        title=planned_title,
        genre=planned_genre,
        tone=planned_tone,
        intro=planned_intro,
        volume_count=vols,
        chapters_per_volume=cpv,
        words_per_chapter=words,
        character_count=characters,
        key_elements=key_elements,
    )


def plan_from_checkpoint(
    ckpt: dict[str, object],
    *,
    title: str,
    genre: str,
    tone: str,
    intro: str,
    key_elements: str = "[]",
    target_words: int | None = None,
) -> NovelPlan | None:
    if not all(k in ckpt for k in _SCALE_KEYS):
        return None
    volumes = _as_int(ckpt.get("volume_count"), 0)
    chapters = _as_int(ckpt.get("chapters_per_volume"), 0)
    words = _as_int(ckpt.get("words_per_chapter"), 0)
    if not (
        VOLUME_MIN <= volumes <= VOLUME_MAX
        and 1 <= chapters <= CHAPTERS_PER_VOLUME_MAX
        and WORDS_PER_CHAPTER_MIN <= words <= WORDS_PER_CHAPTER_MAX
    ):
        raise GenerationError(
            "规划检查点规模无效或超出当前容量，请重新规划；已有内容不会被改写"
        )
    if target_words is not None:
        validate_target_words(target_words)
        planned_words = volumes * chapters * words
        if planned_words < target_words:
            raise GenerationError(
                f"旧规划检查点仅覆盖 {planned_words} 字，低于当前目标字数 {target_words}；"
                "请重新规划分卷和章节后再生成，已有内容不会被改写"
            )
    return NovelPlan(
        title=title,
        genre=genre,
        tone=tone,
        intro=intro,
        volume_count=volumes,
        chapters_per_volume=chapters,
        words_per_chapter=words,
        character_count=_clamp(
            _as_int(ckpt.get("character_count"), 5), CHARACTER_MIN, CHARACTER_MAX
        ),
        key_elements=_parse_key_elements(_safe_json_list(key_elements)),
    )


def _safe_json_list(raw: str) -> list[object]:
    try:
        parsed = cast(object, json.loads(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return cast(list[object], parsed)


def fallback_title(brief: str) -> str:
    line = brief.strip().splitlines()[0].strip() if brief.strip() else ""
    return line[:40] if line else "未命名"


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
    # key_elements 是从 brief 派生的提取结果,不是用户直接填写的字段,始终以最新规划结果覆盖。
    book.key_elements = json.dumps(plan.key_elements, ensure_ascii=False)


async def plan_novel(
    brief: str,
    *,
    title: str = "",
    genre: str = "",
    tone: str = "",
    target_words: int = 100000,
) -> NovelPlan:
    validate_target_words(target_words)
    llm = get_llm()

    def _safe(value: str) -> str:
        return value.replace("{", "{{").replace("}", "}}")

    user = prompts.PLANNER_USER.format(
        brief=_safe(brief),
        title=_safe(title or "由你拟定"),
        genre=_safe(genre or "由你拟定"),
        tone=_safe(tone or "由你拟定"),
        target_words=target_words,
        volume_max=VOLUME_MAX,
        chapters_per_volume_max=CHAPTERS_PER_VOLUME_MAX,
        words_per_chapter_min=WORDS_PER_CHAPTER_MIN,
        words_per_chapter_max=WORDS_PER_CHAPTER_MAX,
    )
    text = await llm.chat(user, system=prompts.PLANNER_SYSTEM)
    blob = text.strip()
    if fenced := re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", blob, re.DOTALL):
        blob = fenced[1]
    try:
        raw = _as_object_dict(cast(object, json.loads(blob)))
    except json.JSONDecodeError:
        raise stages.InvalidStage("模型未返回有效的规划 JSON，请重试") from None
    if raw is None:
        raise stages.InvalidStage("模型规划必须返回一个 JSON 对象，请重试")
    for field, maximum in (
        ("title", 255),
        ("genre", 128),
        ("tone", 128),
        ("intro", 200),
    ):
        value = raw.get(field)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
            raise stages.InvalidStage(
                f"模型规划字段 {field} 缺失或不是有效文本，请重试"
            )
    for field, lower, upper in (
        ("volume_count", VOLUME_MIN, VOLUME_MAX),
        ("chapters_per_volume", 1, CHAPTERS_PER_VOLUME_MAX),
        ("words_per_chapter", WORDS_PER_CHAPTER_MIN, WORDS_PER_CHAPTER_MAX),
        ("character_count", CHARACTER_MIN, CHARACTER_MAX),
    ):
        value = raw.get(field)
        if type(value) is not int or not lower <= value <= upper:
            raise stages.InvalidStage(
                f"模型规划字段 {field} 必须是 {lower} 到 {upper} 的整数，请重试"
            )
    elements = raw.get("key_elements")
    if (
        not isinstance(elements, list)
        or len(elements) > KEY_ELEMENTS_MAX
        or any(
            not isinstance(item, str) or not item.strip()
            for item in cast(list[object], elements)
        )
    ):
        raise stages.InvalidStage(
            "模型规划字段 key_elements 必须是有效的文本数组，请重试"
        )
    return clamp_plan(
        raw,
        target_words=target_words,
        title=title,
        genre=genre,
        tone=tone,
        brief=brief,
    )
